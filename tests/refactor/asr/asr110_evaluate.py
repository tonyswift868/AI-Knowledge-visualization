"""Opt-in local hotword comparison; never modifies product or shared environments.

Prepare with --prepare, run asr110_audio.ps1, then --noise. Evaluate one backend
per process (--backend sensevoice/zipformer/whisper) to isolate RSS and load time.
Models must already be downloaded; this script never downloads them.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
import wave
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.dont_write_bytecode=True
if hasattr(sys.stdout,'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path=Path(path)
    pending=path.with_name(path.name+'.tmp')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)


def product_leaf(name):
    # ASR package __init__ imports Qt service; evaluating these pure functions
    # does not require installing a second copy of Qt in the isolated venv.
    spec=importlib.util.spec_from_file_location('asr110_'+name,
        REPO/'refactor/learning/voice/asr'/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def norm(text):
    return ''.join(re.findall(r'[a-z0-9\u4e00-\u9fff]', str(text).lower()))


def distance(a, b):
    prev = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(cur[-1]+1, prev[j]+1, prev[j-1]+(x != y)))
        prev = cur
    return prev[-1]


def scores(reference, predicted, terms):
    r, p = norm(reference), norm(predicted)
    return dict(errors=distance(r, p), characters=len(r),
                terms_total=len(terms), terms_correct=sum(norm(t) in p for t in terms))


def zip_hotwords(terms, supported):
    # Sherpa rejects the entire supplied list if ONE term cannot be encoded.
    # This particular bilingual checkpoint has no digits/hyphen tokens.
    return [t.upper() for t in terms if re.fullmatch(r'[A-Za-z\u4e00-\u9fff ]+',t)
            and all(c in supported for c in t if '\u4e00' <= c <= '\u9fff')]


def prepare(root, prior):
    rows = read(prior/'sentences.json')
    local = {r['id']: r for r in read(prior/'local.json')}
    for row in rows:
        row['vocabulary'] = local[row['id']]['terms']
        row['group'] = 'original40'
    extra = read(prior/'text-cases.json')
    extra[0]['terms'] = ['向量']  # text-cases.terms was vocabulary, not spoken gold.
    extra[1]['text'] = ('GraphRAG 它是用于一些比较抽象的关系，它可以去建模，然后把它关联起来；'
                        '而传统的向量 RAG 对于一些比较明确的问题比较擅长，所以各有各的用武之地。')
    extra[1]['terms'] = ['GraphRAG', '向量 RAG']
    for row in extra:
        row['vocabulary'] = local['S01']['terms']
        row['group'] = 'inferred_user_meaning'
    rows.extend(extra)
    for i, text in enumerate(['那个我还没懂', '这个可以再讲一次吗', '我不知道', '不是这个意思',
                              '让我想一想', '应该是另外一边', '我刚才说错了', '这个和那个有什么区别'], 1):
        rows.append(dict(id=f'C{i:02}', text=text, terms=[], vocabulary=local['S01']['terms'],
                         question=local['S01']['question'], group='oral_control'))
    write(root/'sentences-input.json', rows)
    digest=hashlib.sha256((prior/'sentences.json').read_bytes()).hexdigest()
    write(root/'input-provenance.json', dict(original40_sha256=digest,
          reconstructed_long_text=True, reconstructed_text_is_not_real_audio_truth=True))


def noise(root):
    import numpy as np
    rows = read(root/'audio-index.json')
    clean = [r for r in rows if r['noise'] == 'clean']
    rng = np.random.default_rng(110)
    for row in clean:
        with wave.open(row['path']) as w:
            params=w.getparams(); a=np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(float)
        # Deterministic stationary background noise, 25 dB RMS SNR over the file.
        std=max(1., float(np.sqrt(np.mean(a*a))))/(10**(25/20))
        b=np.clip(a+rng.normal(0,std,len(a)), -32768,32767).astype('<i2')
        path=root/'wav'/(row['id']+'-noise.wav')
        with wave.open(str(path),'wb') as w:w.setparams(params);w.writeframes(b.tobytes())
        rows.append(dict(row, id=row['id']+'-noise', path=str(path), noise='25dB'))
    user=root/'user-recordings'
    if user.exists():
        for path in sorted(user.glob('*.wav')):
            with wave.open(str(path)) as w:
                if w.getframerate()!=16000 or w.getnchannels()!=1 or w.getsampwidth()!=2:
                    raise ValueError('User WAV must be 16 kHz mono PCM16: '+path.name)
            rows.append(dict(id='real-'+path.stem, sentence_id='real-'+path.stem,
                             path=str(path), voice='user', rate=None, noise='real'))
    write(root/'audio-index.json', rows)


def evaluate(root, backend, model, limit=None):
    import numpy as np
    try:
        import psutil
        process=psutil.Process()
    except ImportError:
        # Existing product Python need not acquire evaluation-only packages.
        import ctypes
        from types import SimpleNamespace
        class MemoryCounters(ctypes.Structure):
            _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong),
                      *[(n,ctypes.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
                      'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]]
        def memory_info():
            counters=MemoryCounters();counters.cb=ctypes.sizeof(counters)
            current=ctypes.windll.kernel32.GetCurrentProcess;current.restype=ctypes.c_void_p
            get=ctypes.windll.psapi.GetProcessMemoryInfo
            get.argtypes=[ctypes.c_void_p,ctypes.POINTER(MemoryCounters),ctypes.c_ulong]
            if not get(current(),ctypes.byref(counters),counters.cb):raise ctypes.WinError()
            return SimpleNamespace(rss=counters.WorkingSetSize)
        process=SimpleNamespace(memory_info=memory_info)
    correct=product_leaf('glossary').correct
    protected_vocabulary=product_leaf('lesson').protected_vocabulary
    corpus=read(root.parent/'asr109/corpus.json')
    protected=protected_vocabulary([c['text'] for c in corpus['chunks']])
    sentences={r['id']:r for r in read(root/'sentences-input.json')}
    before=process.memory_info().rss
    started=time.perf_counter()
    if backend == 'product-sensevoice':
        from refactor.learning.voice.asr.engine import AsrEngine
        first=AsrEngine(model)
        if not first.ready.wait(30):raise TimeoutError('Product recognizer load')
        first_load=time.perf_counter()-started
        state=[first]
        def decode(audio, terms):
            engine=state.pop() if state else AsrEngine(model)
            if not engine.ready.wait(30):raise TimeoutError('Product recognizer load')
            pcm=(audio*32768).astype('<i2').tobytes();begin=time.perf_counter()
            try:
                for offset in range(0,len(pcm),640):
                    due=begin+offset/32000
                    if due>time.perf_counter():time.sleep(due-time.perf_counter())
                    engine.feed(pcm[offset:offset+640])
                due=begin+len(pcm)/32000
                if due>time.perf_counter():time.sleep(due-time.perf_counter())
                stopped=time.perf_counter();text=engine.finish().result(timeout=30)
                seconds=time.perf_counter()-stopped
                return text,seconds,engine.load_seconds
            finally:engine.wait_closed(5)
    elif backend == 'sensevoice':
        import sherpa_onnx
        rec=sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model/'model.int8.onnx'),tokens=str(model/'tokens.txt'),
            num_threads=4,provider='cpu',language='auto',use_itn=True)
        def decode(audio, terms):
            stream=rec.create_stream();stream.accept_waveform(16000,audio)
            rec.decode_stream(stream);return stream.result.text
    elif backend in {'zipformer','zipformer-paced'}:
        import sherpa_onnx
        supported={line.split()[0] for line in (model/'tokens.txt').read_text(encoding='utf-8').splitlines()}
        rec=sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=str(model/'encoder-epoch-99-avg-1.int8.onnx'),
            decoder=str(model/'decoder-epoch-99-avg-1.onnx'),
            joiner=str(model/'joiner-epoch-99-avg-1.int8.onnx'),
            tokens=str(model/'tokens.txt'),num_threads=4,provider='cpu',
            modeling_unit='cjkchar+bpe',bpe_vocab=str(model/'bpe.vocab'),
            decoding_method='modified_beam_search',max_active_paths=4,hotwords_score=1.5)
        def decode(audio, terms):
            # One new stream per utterance: hotwords change without reloading weights.
            stream=rec.create_stream(hotwords='/'.join(zip_hotwords(terms,supported)))
            if backend=='zipformer-paced':
                begin=time.perf_counter()
                for offset in range(0,len(audio),320):
                    due=begin+offset/16000
                    if due>time.perf_counter():time.sleep(due-time.perf_counter())
                    stream.accept_waveform(16000,audio[offset:offset+320])
                    while rec.is_ready(stream):rec.decode_stream(stream)
                due=begin+len(audio)/16000
                if due>time.perf_counter():time.sleep(due-time.perf_counter())
                stopped=time.perf_counter()
                stream.accept_waveform(16000,np.zeros(8000,dtype=np.float32));stream.input_finished()
                while rec.is_ready(stream):rec.decode_stream(stream)
                return rec.get_result(stream),time.perf_counter()-stopped,None
            stream.accept_waveform(16000,np.concatenate([audio,np.zeros(8000,dtype=np.float32)]))
            stream.input_finished()
            while rec.is_ready(stream):rec.decode_stream(stream)
            return rec.get_result(stream)
    elif backend == 'whisper':
        from faster_whisper import WhisperModel
        rec=WhisperModel(str(model),device='cpu',compute_type='int8',cpu_threads=4,
                         num_workers=1,local_files_only=True)
        def decode(audio, terms):
            segments,_=rec.transcribe(audio,language='zh',beam_size=5,temperature=0,
                # Whisper retains only the last 223 prompt tokens. Explicitly
                # cap from the front so current-point terms aren't discarded.
                initial_prompt=rec.hf_tokenizer.encode('本课术语：'+'、'.join(terms),add_special_tokens=False).ids[:200],
                condition_on_previous_text=False,
                vad_filter=False,word_timestamps=False)
            return ''.join(s.text for s in segments)
    else:raise ValueError(backend)
    meta=dict(backend=backend,load_seconds=time.perf_counter()-started,
              loaded_rss_bytes=process.memory_info().rss,load_rss_delta=process.memory_info().rss-before,
              model_bytes=sum(p.stat().st_size for p in model.rglob('*') if p.is_file()),
              measurement='warm CPU, buffered WAV supplied at recording stop; full decode wall time, no audio playback')
    if backend=='product-sensevoice':
        meta['measurement']='actual AsrEngine, fresh load each recording, PCM paced at 20 ms, seconds measured after recording stop'
    elif backend=='zipformer-paced':
        meta['measurement']='resident OnlineRecognizer, PCM paced at 20 ms, tail decode measured after recording stop'
    write(root/(backend+'-meta.json'),meta)
    output=[]
    for audio_row in read(root/'audio-index.json')[:limit]:
        row=sentences.get(audio_row['sentence_id'])
        if row is None:
            row=dict(id=audio_row['sentence_id'],text=Path(audio_row['path']).stem,terms=[],
                     vocabulary=sentences['S01']['vocabulary'],question='',group='real_user')
        with wave.open(audio_row['path']) as w:
            assert w.getframerate()==16000 and w.getnchannels()==1 and w.getsampwidth()==2
            audio=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)/32768.
        t=time.perf_counter();value=decode(audio,row['vocabulary']);seconds=time.perf_counter()-t
        per_record_load=None
        if backend in {'product-sensevoice','zipformer-paced'}:raw,seconds,per_record_load=value
        else:raw=value
        t=time.perf_counter()
        local,edits=correct(raw,row['vocabulary'],protect_existing=False,
                            protected_terms=protected,lesson_match=True)
        local_seconds=time.perf_counter()-t
        output.append(dict(audio_row,group=row['group'],reference=row['text'],gold_terms=row['terms'],
            raw=raw,local=local,edits=list(edits),seconds=seconds,local_seconds=local_seconds,
            duration=len(audio)/16000,per_record_load=per_record_load,raw_scores=scores(row['text'],raw,row['terms']),
            local_scores=scores(row['text'],local,row['terms']),rss_bytes=process.memory_info().rss))
        write(root/(backend+'-results.json'),output)
        print(backend,audio_row['id'],round(seconds,3),raw,'=>',local,flush=True)
    meta['peak_rss_bytes']=max([meta['loaded_rss_bytes'],*[r['rss_bytes'] for r in output]])
    write(root/(backend+'-meta.json'),meta)


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--prior',type=Path);p.add_argument('--prepare',action='store_true')
    p.add_argument('--noise',action='store_true');p.add_argument('--backend',choices=['sensevoice','product-sensevoice','zipformer','zipformer-paced','whisper'])
    p.add_argument('--model',type=Path);p.add_argument('--limit',type=int)
    a=p.parse_args();root=a.root.resolve()
    if not root.is_relative_to(Path('D:/MentorFlow/.local/test-tmp/asr110').resolve()):
        raise ValueError('Use the isolated task directory')
    root.mkdir(parents=True,exist_ok=True)
    if a.prepare:prepare(root,a.prior)
    elif a.noise:noise(root)
    else:evaluate(root,a.backend,a.model,a.limit)


if __name__=='__main__':main()
