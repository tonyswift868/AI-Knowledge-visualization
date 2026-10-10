import * as React from 'react'
import {createRoot} from 'react-dom/client'
import {RecommendationCards, formatStars} from '../../../refactor/learning/web/src/pages/NewPlan'
import '../../../refactor/learning/web/src/theme.css'

const cards: any[] = [
  {card_id:'repo',title:'Python 数据分析文字教程',type:'仓库',url:'https://github.com/example/python',why:'完整文字教程',modules:['数据清洗'],github_stars:12300},
  {card_id:'nostars',title:'计数获取失败也正常显示',type:'仓库',url:'https://github.com/example/unknown',why:'完整文字资料',modules:['数据探索']},
  {card_id:'video',title:'没有文字时的视频兜底',type:'课程',url:'https://youtube.com/playlist?list=1',why:'暂无可用文字资料',modules:['演示'],is_video:true}
]
createRoot(document.getElementById('root')!).render(<RecommendationCards cards={cards} onUse={() => {}} />)
setTimeout(()=>{
  const text = document.body.textContent || ''
  const links = Array.from(document.querySelectorAll('a'))
  ;(window as any).plan37Result = {ok:text.includes('★ 12.3k') && text.includes('视频，难以生成知识点') &&
    (text.match(/★/g)||[]).length === 1 && links[0]?.href === cards[0].url &&
    formatStars(0)==='0' && formatStars(999)==='999' && formatStars(1200000)==='1.2m', text}
},100)
