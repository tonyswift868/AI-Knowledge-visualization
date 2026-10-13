/** Standalone fixture only: never imported by the product router. */
import * as React from 'react'
import { createRoot } from 'react-dom/client'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { ClassroomMarkdown, cjkStrong } from '../../../refactor/learning/web/src/classroom/Markdown'
import { RecapCard } from '../../../refactor/learning/web/src/classroom/RecapCard'
import '../../../refactor/learning/web/src/theme.css'
import './preview117.css'

const prose = `把检索想成找书：先从很多内容里，找到与你的问题最相关的那几段。

==**向量检索**擅长按意思找相近的内容。== 比如你问“怎样节省电量”，也能找到“省电小技巧”。

- 先把文字变成向量，再比较距离。
- ==相关不代表答案一定正确，还要回到原文核对。==

这一步不用背公式；理解“按意思找”就够了。[〔3〕](mf-source:chunk-3)

如果只想找一句原话，你会先用哪种检索？`
const old = '**术语**照常加粗。\n\n- 原来的列表\n- 原来的第二项\n\n[〔3〕](mf-source:chunk-3)\n\n| 项目 | 数值 |\n| --- | --- |\n| 示例 | 2 |\n\n行内 `a == b` 与 $x=1$。'
const code = '行内 `a == b`。\n\n```python\nif a == b:\n    print("==不标记==")\n```\n\n外面 ==才是重点==。'
const recap = {title:'向量检索：记住这三点',items:[
  {title:'怎么找',text:'按语义相似度找相关内容。',chunk_id:'chunk-1'},
  {title:'适合什么',text:'从一段或几段资料里定位明确的信息。',chunk_id:'chunk-2'},
  {title:'不要混淆',text:'相关内容不等于正确答案，要核对依据。',chunk_id:''},
]}
const callbacks: string[] = []
const onSource = (id: string) => {callbacks.push(id)}

function Legacy({content}:{content:string}) {
  return <ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[rehypeKatex]}
    urlTransform={url=>url.startsWith('mf-source:')?url:defaultUrlTransform(url)}
    components={{strong:({children})=><strong>{React.Children.map(children,c=>typeof c==='string'?c.replaceAll('\u2060',''):c)}</strong>,
      a:({href,children})=>href?.startsWith('mf-source:')?<button className="cite" title="看原文" onClick={()=>onSource(href.slice(10))}>{children}</button>:<a href={href} target="_blank" rel="noreferrer">{children}</a>}}>
    {cjkStrong(content)}</ReactMarkdown>
}
function Preview() {
  const [focus,setFocus]=React.useState(true)
  const [stream,setStream]=React.useState('开头 =')
  const [key,setKey]=React.useState('message-1')
  React.useEffect(()=>{
    Object.assign(window,{focus117SetStream:setStream,focus117SetKey:setKey,
      focus117Theme:(theme:string)=>{document.documentElement.dataset.theme=theme}})
  },[])
  return <main className="preview117">
    <header className="preview117-header"><div><span className="preview117-eyebrow">课堂 · 精炼显示 / 组件预览</span><h1>少读一点，抓住重点</h1></div>
      <label className="preview117-switch"><input type="checkbox" checked={focus} onChange={e=>setFocus(e.target.checked)} />只看重点</label></header>
    <div className="preview117-grid"><section><h2>01 · 荧光笔与重点模式</h2><article className="bubble bot" data-test="focus">
      <ClassroomMarkdown content={prose} focus={focus} messageKey={key} onSource={onSource} /></article></section>
      <section><h2>02 · 讲完一大点，用卡片收尾</h2><RecapCard content={recap} onSource={onSource} />
        <aside className="preview117-note">每个小要点一行结论；需要依据时，再点“原文”。</aside></section></div>
    <section className="preview117-stream"><h2>03 · 生成中的重点句</h2><article className="bubble bot" data-test="stream"><ClassroomMarkdown content={stream} /></article>
      <small>标记未闭合时原样显示，闭合后才出现荧光。</small></section>
    <div className="preview117-contract" aria-hidden="true">
      <article data-test="code"><ClassroomMarkdown content={code} /></article>
      <article data-test="old"><ClassroomMarkdown content={old} onSource={onSource} /></article>
      <article data-test="legacy"><Legacy content={old} /></article>
      <article data-test="escaped"><ClassroomMarkdown content={'\\==不是重点==，&#61;&#61;也不是重点&#61;&#61;'} /></article>
      <article data-test="list-question"><ClassroomMarkdown content={'- 补充说明\n- 你会怎样做？'} focus /></article>
    </div>
  </main>
}
createRoot(document.getElementById('root')!).render(<Preview />)
const until = async (fn:()=>boolean) => {const end=performance.now()+5000;while(!fn()){if(performance.now()>end)throw Error('UI condition timeout');await new Promise(r=>requestAnimationFrame(r))}}
function assert(value:unknown,message:string):asserts value {if(!value)throw Error(message)}
const visible=(e:Element|null)=>!!e && !!e.getClientRects().length
async function run() {
  await until(()=>!!(window as any).focus117SetStream)
  const area=document.querySelector('[data-test="focus"]')!
  assert(area.querySelectorAll('mark.hl').length===2,'Expected paired highlights')
  assert(area.querySelector('mark.hl strong')?.textContent==='向量检索','Bold term inside highlight lost')
  const first=area.querySelector('.focus-block')!
  assert(!visible(first.querySelector('.focus-body')),'Unmarked paragraph not folded')
  const button=first.querySelector<HTMLButtonElement>('button')!
  assert(button.textContent==='…（点开）','Fold placeholder missing')
  button.click();await until(()=>button.getAttribute('aria-expanded')==='true')
  assert(visible(first.querySelector('.focus-body')),'Expanded prose invisible')
  assert(first.textContent?.includes('把检索想成找书'),'Expanded prose missing')
  const question=area.querySelector('[data-focus-question]')!
  assert(visible(question) && question.textContent?.endsWith('？'),'Question folded')
  const lis=[...area.querySelectorAll('li')]
  assert(!visible(lis[0].querySelector('.focus-body')),'Unmarked list item not folded')
  assert(visible(lis[1].querySelector('.focus-body')),'Highlighted list item folded')
  assert(visible(document.querySelector('[data-test="list-question"] [data-focus-question]')),'Tight-list question folded')
  const highlighted=area.querySelector('mark.hl')!
  assert(getComputedStyle(highlighted).color!==getComputedStyle(highlighted.closest('.focus-body')!).color,'Non-highlighted prose not dimmed')
  const stream=document.querySelector('[data-test="stream"]')!
  for(const content of ['开头 =','开头 ==','开头 ==这句还在生成']) {
    ;(window as any).focus117SetStream(content)
    await until(()=>stream.textContent===content)
    assert(!stream.querySelector('mark'),'Unclosed stream marked or swallowed')
  }
  ;(window as any).focus117SetStream('开头 ==这句还在生成==')
  await until(()=>stream.querySelector('mark.hl')?.textContent==='这句还在生成')
  const codes=document.querySelector('[data-test="code"]')!
  assert(codes.querySelectorAll('mark').length===1,'Code delimiters transformed')
  assert(codes.querySelector('code')?.textContent==='a == b','Inline code changed')
  assert(codes.querySelector('pre code')?.textContent?.includes('==不标记=='),'Fenced code changed')
  assert(!document.querySelector('[data-test="escaped"] mark'),'Escaped/entity syntax transformed')
  assert(document.querySelector('[data-test="old"]')!.innerHTML===document.querySelector('[data-test="legacy"]')!.innerHTML,'Legacy message DOM changed')
  const recap=document.querySelector('.recap-card')!
  assert(recap.querySelectorAll('li').length===3 && recap.querySelectorAll('button').length===2,'Recap row/source counts')
  recap.querySelector<HTMLButtonElement>('button')!.click()
  assert(callbacks.at(-1)==='chunk-1','Wrong chunk callback')
  area.querySelector<HTMLButtonElement>('.cite')!.click()
  assert(callbacks.at(-1)==='chunk-3','Existing citation callback changed')
  ;(window as any).focus117SetKey('message-2')
  await until(()=>area.querySelector('.focus-reveal')?.getAttribute('aria-expanded')==='false')
  assert(!visible(area.querySelector('.focus-body')),'Open state leaked to another message')
  const toggle=document.querySelector<HTMLInputElement>('.preview117-switch input')!
  toggle.click();await until(()=>!area.querySelector('.focus-content'))
  assert(!area.querySelector('.focus-reveal') && area.textContent?.includes('把检索想成找书'),'Full mode not restored')
  toggle.click();await until(()=>!!area.querySelector('.focus-content'))
  Object.assign(window,{focus117Result:{ok:true,checks:['荧光与嵌套加粗','流式未闭合/闭合','代码与转义保护','段落和列表折叠展开','问题句保留','非重点变淡','旧消息DOM一致','要点卡与原文回调','消息隔离/切回全文']}})
}
run().catch(e=>{(window as any).focus117Result={ok:false,error:String(e.stack||e)}})
