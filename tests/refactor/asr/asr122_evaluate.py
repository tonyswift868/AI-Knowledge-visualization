"""Explicit local replay through the real AsrEngine, PCM paced at 20 ms.

Uses the frozen 109/110 corpus and existing 110 metrics; never collects in pytest.
No model downloads, microphones, application startup or credential exports.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import wave

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.dont_write_bytecode = True
from tests.refactor.asr.asr110_evaluate import prepare as prepare110, noise, read, write, scores


def baseline(root):
    name = 'refactor.learning.voice.asr.engine122_baseline'
    spec = importlib.util.spec_from_file_location(name, root/'baseline-engine.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    prepare110(root, root.parent/'asr109')
    (root/'baseline-engine.py').write_bytes(subprocess.check_output(
        ['git', 'show', 'efbb0670:refactor/learning/voice/asr/engine.py'], cwd=REPO))
    # Separate SAPI fragments create a real internal pause, not a VAD seam.
    cases = [('向量', '那个', ['向量']), ('Graph', 'RAG 那边', ['GraphRAG']),
             ('用 Agent', '当场决定', ['Agent']), ('工作流', '提前写死', ['工作流']),
             ('Chunk', '切太大了', ['Chunk']), ('向', '量检索更合适', ['向量']),
             ('GraphRAG', '会把关系关联起来', ['GraphRAG']),
             ('知识图谱', '用来表达关系', ['知识图谱']),
             ('数据库', '存放这些数据', ['数据库']), ('RAG', '先检索再回答', ['RAG'])]
    base = read(root/'sentences-input.json')[0]
    pauses = [dict(id=f'P{i:02}', text=a+b, fragments=[a,b], pause=.8+(i-1)%4*.2,
                   terms=terms, vocabulary=base['vocabulary'], question=base['question'], group='pause')
              for i,(a,b,terms) in enumerate(cases,1)]
    write(root/'pause-input.json', pauses)
    (root/'report.md').write_text(
        '# 122-MF02 语音整段终稿评估\n\n'
        '基线 efbb0670；不换模型、不下载模型、不动用户软件。\n\n'
        '运行前预期：最终整段识别保留前后文，应接近110整段一次识别结果；'
        '①+②术语≥90%、字错误率≤6%、新增误换≤1、停止后最慢≤3秒。'
        '不按测试句调参数；合成语音比真人干净。②仅纠正已有读音，不补答案，'
        '相同输入复用历史结果，新增调用总上限¥0.10。\n', encoding='utf-8')


def post_audio(root):
    noise(root)
    rows = read(root/'audio-index.json')
    for case in read(root/'pause-input.json'):
        fragments=[]
        for i in range(2):
            with wave.open(str(root/'wav'/f'{case["id"]}-{i}.wav')) as w:
                a=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').copy()
            # Remove SAPI's own boundary silence; controlled pause is exact.
            voiced=np.flatnonzero(np.abs(a)>100)
            fragments.append(a[max(0,int(voiced[0]) - 800):int(voiced[-1])+801] if len(voiced) else a)
        audio=np.concatenate([fragments[0], np.zeros(round(case['pause']*16000), dtype='<i2'),fragments[1]])
        path=root/'wav'/(case['id']+'.wav')
        with wave.open(str(path),'wb') as w:
            w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(audio.tobytes())
        rows.append(dict(id=case['id'],sentence_id=case['id'],path=str(path),voice='Microsoft Huihui Desktop',
                         rate=0,noise='clean',pause=case['pause']))
    for row in rows:
        row['sha256']=hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()
    write(root/'audio-index.json',rows)


def model_path(root):
    return Path(read(root.parent/'asr109/environment.json')['model_path'])


def calibrate(root):
    old=baseline(root);backend=old._SenseVoiceBackend(model_path(root),threading.Event())
    with wave.open(str(root/'wav/U02-v0-r0.wav')) as w:
        a=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)/32768
    rows=[]
    for seconds in [10,20,25,30,45]:
        audio=np.tile(a,int(np.ceil(seconds*16000/len(a))))[:seconds*16000]
        times=[]
        for _ in range(2):
            t=time.perf_counter();text=backend.decode(audio);times.append(time.perf_counter()-t)
        rows.append(dict(seconds=seconds,decode_seconds=times,text=text))
        print(seconds,times,flush=True)
    write(root/'calibration-results.json',rows)


def evaluate(root, version, group=None):
    from refactor.learning.voice.asr.glossary import correct
    from refactor.learning.voice.asr.lesson import protected_vocabulary
    if version=='before':engine_class=baseline(root).AsrEngine
    else:
        from refactor.learning.voice.asr.engine import AsrEngine
        engine_class=AsrEngine
    sentences={r['id']:r for r in read(root/'sentences-input.json')+read(root/'pause-input.json')}
    protected=protected_vocabulary([c['text'] for c in read(root.parent/'asr109/corpus.json')['chunks']])
    output_path=root/(version+'-results.json')
    output=read(output_path) if output_path.exists() else []
    completed={r['id'] for r in output}
    for audio_row in read(root/'audio-index.json'):
        if audio_row['id'] in completed:continue
        row=sentences[audio_row['sentence_id']]
        if group and row['group']!=group:continue
        with wave.open(audio_row['path']) as w:pcm=w.readframes(w.getnframes())
        engine=engine_class(model_path(root))
        assert engine.ready.wait(30), engine.failure
        started=time.perf_counter()
        try:
            for offset in range(0,len(pcm),640):
                due=started+offset/32000
                if due>time.perf_counter():time.sleep(due-time.perf_counter())
                engine.feed(pcm[offset:offset+640])
            due=started+len(pcm)/32000
            if due>time.perf_counter():time.sleep(due-time.perf_counter())
            stopped=time.perf_counter();raw=engine.finish().result(timeout=30)
            seconds=time.perf_counter()-stopped
            t=time.perf_counter();local,edits=correct(raw,row['vocabulary'],protect_existing=False,
                           protected_terms=protected,lesson_match=True)
            local_seconds=time.perf_counter()-t
            output.append(dict(audio_row,group=row['group'],reference=row['text'],gold_terms=row['terms'],
                vocabulary=row['vocabulary'],question=row.get('question',''),raw=raw,local=local,edits=edits,
                seconds=seconds,local_seconds=local_seconds,duration=len(pcm)/32000,
                first_speech_sample=engine.first_speech_sample,
                whole_outcome=getattr(engine,'whole_outcome','segmented'),
                whole_blocks=getattr(engine,'whole_block_seconds',[]),
                raw_scores=scores(row['text'],raw,row['terms']),local_scores=scores(row['text'],local,row['terms'])))
            write(output_path,output)
            print(version,row['id'],audio_row['id'],round(seconds,3),raw,'=>',local,flush=True)
        finally:
            engine.wait_closed(5);engine.cancel()


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','post-audio','calibrate','before','after'])
    p.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/asr122'))
    p.add_argument('--group');a=p.parse_args();root=a.root.resolve()
    assert root==Path('D:/MentorFlow/.local/test-tmp/asr122').resolve()
    if a.action=='prepare':prepare(root)
    elif a.action=='post-audio':post_audio(root)
    elif a.action=='calibrate':calibrate(root)
    else:evaluate(root,a.action,a.group)


if __name__=='__main__':main()
