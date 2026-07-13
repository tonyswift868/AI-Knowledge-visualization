from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import db
from app.schemas import GoalProfile, WorkspaceIn
from app.services import analyze, build_plan, create_source, uid

def seed():
    db.init_db()
    with db.connection() as conn:
        conn.execute("DELETE FROM workspaces WHERE title = ?", ("Demo · Git 合并基础",))
    now = db.now(); ws = {"id":uid('ws'),"title":"Demo · Git 合并基础","description":"固定结构的 MentorFlow 演示空间","status":"active","character_id":"warm","allow_external_knowledge":0,"created_at":now,"updated_at":now}
    with db.connection() as conn: conn.execute("INSERT INTO workspaces VALUES (:id,:title,:description,:status,:character_id,:allow_external_knowledge,:created_at,:updated_at)", ws)
    text = (Path(__file__).resolve().parents[1] / 'demo' / 'demo_source.md').read_text(encoding='utf-8')
    create_source(ws['id'], 'Git 分支与安全合并基础', 'demo', text, trust_level='reference')
    goal = GoalProfile(goal_statement='在两周内掌握 Git 安全合并流程并完成一份检查清单', motivation='减少团队协作中的合并风险', deadline=__import__('datetime').date.today()+__import__('datetime').timedelta(days=14), minutes_per_day=30, days_per_week=5, current_level='beginner', expected_output='一份可执行的合并检查清单', success_criteria=['能独立解释合并前检查步骤'])
    with db.connection() as conn: conn.execute("INSERT INTO goals VALUES (?,?,?,?,?,?)", (uid('goal'), ws['id'], __import__('json').dumps(goal.model_dump(mode='json'),ensure_ascii=False), 1, now, now))
    assessment = analyze(ws['id'], goal.model_dump(mode='json')); assessment['user_confirmed'] = True
    plan = build_plan(ws['id'], goal.model_dump(mode='json'), assessment)
    with db.connection() as conn: conn.execute("UPDATE plans SET status='active', activated_at=? WHERE id=?", (db.now(),plan['id']))
    print(ws['id'])

if __name__ == '__main__': seed()
