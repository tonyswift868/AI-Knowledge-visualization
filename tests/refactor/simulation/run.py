"""python -m tests.refactor.simulation.run --help (live never runs in pytest)."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import shutil

from .actors import Persona,ScriptUser,ModelUser,OfflineGateway,offline_fetch
from .driver import Driver,STAGES
from .engine import Clock,Simulation,SCRATCH


def live_factory(preset_root,account):
    """Copy ONLY existing preset metadata. Never read or export the key."""
    source=Path(preset_root)/'learning'/'accounts'/account
    def create(services):
        from refactor.learning.connections.gateway import ModelGateway
        from refactor.learning.connections.pricing import PriceCatalog
        target=services.data_root/'learning'/'accounts'/account
        target.mkdir(parents=True,exist_ok=True)
        for name in ('model-gateway.json','connections.json'):
            p=source/name
            if not p.exists():continue
            metadata=json.loads(p.read_text(encoding='utf-8'))
            if name=='model-gateway.json' and metadata.get('retired_credentials'):
                raise ValueError('存在待删除凭据元数据，模拟不执行账号凭据清理。')
            if not (target/name).exists():shutil.copyfile(p,target/name)
        prices=Path(__file__).resolve().parents[3]/'refactor/learning/course/data/model-prices.json'
        real=ModelGateway(account,services.data_root,price_catalog=PriceCatalog(prices))
        config=real.get_configuration()
        if not config or config['provider_id']!='deepseek' or config['model'] not in ('deepseek-v4-flash','deepseek-flash'):
            real.close();raise ValueError('真实验证必须选择管理员现有DeepSeek Flash预设。')
        return real
    return create


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True,help='D:/MentorFlow/.local/test-tmp/下独立运行目录')
    parser.add_argument('--live',action='store_true',help='只在已授权任务/预算内使用真实模型')
    parser.add_argument('--persona',type=Path,help='Persona字段JSON；模型用户每次读实时屏幕')
    parser.add_argument('--script',type=Path,help='固定用户动作JSON数组；不读取隐藏答案')
    parser.add_argument('--account-id',default='simulation42')
    parser.add_argument('--preset-root',type=Path,default=Path(r'D:\MentorFlowData\account-shell'))
    parser.add_argument('--budget',default='0.5',help='人民币高峰价预算，产品+模拟用户合计')
    parser.add_argument('--to',choices=STAGES,default='free')
    parser.add_argument('--from-stage',choices=STAGES,help='只能从已到达检查点续跑，不能跳过建计划')
    parser.add_argument('--day',type=int,help='检查点已建计划时，只跑第N天的课')
    parser.add_argument('--dialogue-rounds',type=int,help='填表计第1轮，到指定轮数停止；没有默认对话轮数上限')
    parser.add_argument('--stuck-limit',type=int,default=5)
    parser.add_argument('--timeout',type=float,default=240)
    parser.add_argument('--clock',default='2026-10-03T10:00:00+09:00')
    args=parser.parse_args(argv)
    if args.timeout<=0 or args.stuck_limit<1 or (args.dialogue_rounds is not None and args.dialogue_rounds<1):
        parser.error('超时、无进展次数、指定对话轮数须为正数')
    root=args.root.resolve()
    if not root.is_relative_to(SCRATCH.resolve()):parser.error('运行目录只能在D盘test-tmp下')
    if (root/'data').exists() and not (root/'checkpoint.json').exists():parser.error('已有数据但无检查点，不覆盖或删除')
    fields=json.loads(args.persona.read_text(encoding='utf-8')) if args.persona else {}
    persona=Persona(**fields);clock=Clock(args.clock)
    factory=live_factory(args.preset_root,args.account_id) if args.live else lambda _:OfflineGateway()
    allowed={'sufficiency','plan_intent','simulation_user'} if args.live and args.to=='dialogue' else None
    sim=Simulation(root,clock,factory,account=args.account_id,fetcher=None if args.live else offline_fetch,
        offline=not args.live,budget=args.budget,timeout=args.timeout,allowed=allowed)
    try:
        sim.evidence.save('run-config.json',dict(mode='live' if args.live else 'offline',persona=asdict(persona),
            budget=args.budget,to=args.to,from_stage=args.from_stage,day=args.day,dialogue_rounds=args.dialogue_rounds,
            clock=args.clock,stuck_limit=args.stuck_limit,timeout=args.timeout))
        user=ScriptUser(json.loads(args.script.read_text(encoding='utf-8')) if args.script else None) if not args.live or args.script else ModelUser(sim.gateway,persona)
        driver=Driver(sim,user,persona,stuck_limit=args.stuck_limit)
        summary=driver.run(to=args.to,from_stage=args.from_stage,rounds=args.dialogue_rounds,day=args.day)
        print(json.dumps(summary,ensure_ascii=False,indent=2));return 1 if summary['stop'] else 0
    finally:sim.close()


if __name__=='__main__':raise SystemExit(main())
