"""Static, reviewable data for Learning Expedition V1.

The battle engine consumes these dictionaries as data.  No configuration
value is ever executed as Python/JavaScript.
"""

from __future__ import annotations


def _skill(skill_id: str, name: str, summary: str, effects: list[dict], animation: str) -> dict:
    return {"skill_id": skill_id, "name": name, "summary": summary, "effects": effects, "animation": animation}


def _hero(hero_id: str, name: str, role: str, stats: dict, tags: list[str], normal: dict, active: dict, passive_a: str, passive_b: str, awakening: str, weakness: str, rarity: str = "rare") -> dict:
    return {
        "hero_id": hero_id, "name": name, "rarity": rarity, "role": role, "base_stats": stats,
        "tags": tags, "normal_attack": normal, "active_skill": active,
        "passive_one": {"name": "核心被动", "summary": passive_a},
        "passive_two": {"name": "战斗被动", "summary": passive_b},
        "awakening": {"name": "觉醒", "summary": awakening}, "weakness": weakness,
        "asset_id": f"hero:{hero_id}",
    }


def _dmg(skill_id: str, name: str, multiplier: float, target: str = "front", animation: str = "single_slash", extra: list[dict] | None = None) -> dict:
    return _skill(skill_id, name, f"造成 {int(multiplier * 100)}% 攻击伤害", [{"op": "direct_damage", "target": target, "multiplier": multiplier}, *(extra or [])], animation)


def _stats(hp: int, atk: int, defense: int, hit: float, dodge: float, crit: float, crit_damage: float = 1.5) -> dict:
    return {"HP": hp, "ATK": atk, "DEF": defense, "HIT": hit, "DODGE": dodge, "CRIT_RATE": crit, "CRIT_DAMAGE": crit_damage}


HEROES = [
    _hero("jinxiao", "烬绡", "横排灼烧 / 引爆", _stats(1040, 118, 62, .08, .04, .12), ["burn", "burst"], _dmg("jinxiao_normal", "火刃", .92, extra=[{"op": "chance", "probability": .5, "then": {"op": "apply_status", "status": "burn", "stacks": 1}}]), _dmg("jinxiao_active", "焚线", 1.05, "row", "fire", [{"op": "consume_status_damage", "status": "burn", "per_stack": .35, "max_stacks": 3}, {"op": "spread_status", "status": "burn", "when_consumed": 3}]), "灼烧目标死亡时获得20能量，每轮最多2次", "攻击3层灼烧目标时命中和暴击率提高", "引爆后保留1层灼烧，护盾目标额外受伤", "开场需要叠层，怕净化和快速单体爆发"),
    _hero("chengyin", "澄音", "治疗 / 净化 / 护盾", _stats(1120, 78, 66, .08, .04, .05), ["heal", "cleanse", "shield"], _dmg("chengyin_normal", "清流", .70, extra=[{"op": "modify_energy", "target": "self", "amount": 10}]), _skill("chengyin_active", "澄明回路", "治疗并清除三名队友的负面状态", [{"op": "heal", "target": "lowest_allies", "power": 1.10}, {"op": "cleanse", "target": "lowest_allies", "count": 1}, {"op": "shield", "target": "cleansed_allies", "max_hp_ratio": .06}, {"op": "modify_energy", "target": "cleansed_allies", "amount": 10}], "heal"), "直接伤害降低，治疗提高", "队友清除状态时恢复能量", "若未清除状态，追加治疗与控制保护", "无持续状态时价值下降"),
    _hero("yaye", "鸦夜", "闪避 / 后排刺杀 / 反击", _stats(860, 132, 48, .10, .16, .18), ["dodge", "assassin", "counter"], _dmg("yaye_normal", "夜羽", .95, "last_enemy", extra=[{"op": "add_mark", "target": "self", "mark": "night_trace", "amount": 1}]), _dmg("yaye_active", "夜隙", 1.90, "last_enemy", "single_slash", [{"op": "conditional", "when": "self_dodged", "then": {"op": "force_critical"}}, {"op": "execute_bonus", "threshold": .35, "multiplier": .60}]), "闪避成功后反击并获得夜痕", "主动技能消耗夜痕并穿透部分护盾防御", "首次致命伤害强制闪避", "高命中阵容会让启动困难；自身脆弱"),
    _hero("leishan", "垒山", "护盾 / 分摊 / 嘲讽", _stats(1450, 88, 104, .06, .02, .06), ["shield", "tank", "protect"], _dmg("leishan_normal", "盾击", .82, extra=[{"op": "shield", "target": "self", "max_hp_ratio": .03}]), _skill("leishan_active", "山阵", "为自己和相邻队友提供护盾并分摊伤害", [{"op": "shield", "target": "self_and_adjacent", "max_hp_ratio": .12, "duration": 2}, {"op": "damage_share", "target": "adjacent_allies", "ratio": .25, "duration": 2}], "shield"), "护盾存在时降低暴击伤害", "护盾破裂时横排反击", "首次护盾破裂后再生护盾并嘲讽", "怕驱散、持续伤害和无视护盾效果"),
    _hero("xianlv", "弦律", "能量调度 / 站位联动", _stats(930, 96, 58, .09, .06, .10), ["energy", "tempo", "support"], _dmg("xianlv_normal", "和弦", .80, extra=[{"op": "modify_energy", "target": "lowest_allies", "amount": 8}]), _skill("xianlv_active", "和声调度", "为能量最低队友充能并强化下一行动", [{"op": "modify_energy", "target": "lowest_allies", "amount": 35, "count": 2}, {"op": "modify_stat", "target": "next_ally", "stat": "HIT", "amount": .15}, {"op": "modify_stat", "target": "next_ally", "stat": "CRIT_RATE", "amount": .15}], "energy_wave"), "位于3号位时强化2/4号位", "受到能量削减时只承受70%", "同流派两名角色下一次主动伤害提高", "自身输出低，错误站位收益下降"),
    _hero("gulu", "菌王咕噜", "中毒 / 减疗 / 反制", _stats(970, 106, 54, .07, .06, .08), ["poison", "anti_heal", "random"], _dmg("gulu_normal", "孢子", .75, "random_enemy", "poison", [{"op": "chance", "probability": .55, "then": {"op": "apply_status", "status": "poison", "stacks": 1}}]), _skill("gulu_active", "孢子暴雨", "随机攻击三名敌人并叠加中毒", [{"op": "repeat", "count": 3, "effect": {"op": "direct_damage", "target": "random_enemy", "multiplier": .70}}, {"op": "apply_status", "target": "random_enemies", "status": "poison", "stacks": 1}, {"op": "apply_status", "target": "poisoned_3plus", "status": "anti_heal", "stacks": 1}], "poison"), "中毒目标行动后概率获得能量", "中毒被清除时给清除者孢痕", "主动优先低毒目标并对满层目标追加伤害", "直接伤害低，需要积累"),
    _hero("shuangli", "霜璃", "寒意 / 群控 / 失败补偿", _stats(900, 101, 56, .08, .08, .09), ["freeze", "control", "compensation"], _dmg("shuangli_normal", "霜触", .78, extra=[{"op": "apply_status", "target": "front", "status": "chill", "stacks": 1}]), _skill("shuangli_active", "霜镜散射", "随机攻击三名敌人并独立判定冰冻", [{"op": "repeat", "count": 3, "effect": {"op": "direct_damage", "target": "random_enemy", "multiplier": .85}}, {"op": "apply_status", "target": "random_enemies", "status": "chill", "stacks": 1}, {"op": "chance", "probability": .12, "then": {"op": "apply_status", "status": "freeze", "stacks": 1}}], "ice"), "寒意降低命中，三层触发冰冻判定", "冰冻失败附加寒意并积累冷凝", "冰冻目标首次受击减伤后再解除", "输出低且控制受Resolve限制"),
    _hero("tuantuan", "团团兽", "救援 / 替伤 / 恢复", _stats(1320, 70, 88, .06, .03, .04), ["rescue", "shield", "heal"], _dmg("tuantuan_normal", "滚击", .60, extra=[{"op": "heal", "target": "self", "power": .18}]), _skill("tuantuan_active", "团团救援", "保护生命最低队友并承担下一次攻击", [{"op": "shield", "target": "lowest_ally", "max_hp_ratio": .10}, {"op": "damage_share", "target": "lowest_ally", "ratio": 1.0, "hits": 1}, {"op": "heal", "target": "allies", "power": .02}], "shield"), "成功替伤后使被保护者下次攻击必定命中", "低于40%生命获得减伤", "替伤后被保护者恢复能量", "几乎没有输出和控制"),
    _hero("zhenchui", "震槌", "中心眩晕 / 相邻震荡", _stats(1280, 112, 82, .07, .03, .08), ["stun", "frontline", "armor_break"], _dmg("zhenchui_normal", "震击", .88, extra=[{"op": "chance", "probability": .15, "then": {"op": "modify_energy", "target": "front", "amount": -10}}]), _skill("zhenchui_active", "地脉重击", "重击中心并震荡相邻", [{"op": "direct_damage", "target": "front", "multiplier": 1.50}, {"op": "chance", "probability": .35, "then": {"op": "apply_status", "status": "stun", "stacks": 1}}, {"op": "direct_damage", "target": "front_adjacent", "multiplier": .90}, {"op": "chance", "probability": .15, "then": {"op": "apply_status", "status": "stun", "stacks": 1}}], "aoe_burst"), "无眩晕时恢复能量", "攻击护盾目标额外削盾", "中心目标眩晕失败转能量削减和破甲", "目标分散或1号位死亡时收益下降"),
    _hero("yaoge", "曜歌", "多减益增伤 / 收尾", _stats(900, 108, 52, .09, .06, .13), ["debuff", "aoe", "finisher"], _dmg("yaoge_normal", "余音", .80, "random_enemy", "single_slash", [{"op": "damage_per_debuff", "per": .05, "max": .20}]), _skill("yaoge_active", "终幕合奏", "按目标减益种类增伤的全体攻击", [{"op": "direct_damage", "target": "all_enemies", "multiplier": .75}, {"op": "damage_per_debuff", "per": .12, "max": .48}, {"op": "conditional", "when": "three_debuffs", "then": {"op": "modify_stat", "stat": "CRIT_RATE", "amount": .20}}], "row_slash"), "敌方达到三种减益时获得能量", "不会独自扩散或延长减益", "主动击杀后追加低血目标攻击", "无状态阵容中输出普通，不能独立启动"),
    _hero("xueji", "血棘", "流血 / 行动惩罚 / 收割", _stats(930, 120, 50, .08, .08, .14), ["bleed", "execute", "mark"], _dmg("xueji_normal", "荆刺", .90, extra=[{"op": "chance", "probability": .45, "then": {"op": "apply_status", "status": "bleed", "stacks": 1}}]), _skill("xueji_active", "荆棘断章", "攻击最低生命并按流血层数增伤", [{"op": "direct_damage", "target": "lowest_enemy", "multiplier": 1.75}, {"op": "damage_per_status", "status": "bleed", "per": .15}, {"op": "conditional", "when": "target_below_30", "then": {"op": "modify_stat", "stat": "HIT", "amount": .25}}], "single_slash"), "流血目标使用主动技能时获得能量", "击败后转移一半流血", "未击败时追加流血并降低治疗", "怕净化和高护盾目标"),
    _hero("mochai", "墨裁", "驱散 / 沉默 / 功能辅助", _stats(880, 86, 60, .18, .04, .05), ["dispel", "silence", "utility"], _dmg("mochai_normal", "裁页", .65, extra=[{"op": "chance", "probability": .50, "then": {"op": "dispel_buff", "target": "front", "count": 1}}]), _skill("mochai_active", "禁令", "攻击高能量目标，驱散并概率沉默", [{"op": "direct_damage", "target": "highest_energy_enemies", "multiplier": .70, "count": 2}, {"op": "dispel_buff", "target": "hit_targets", "count": 1}, {"op": "chance", "probability": .25, "then": {"op": "apply_status", "status": "silence", "stacks": 1}}], "energy_wave"), "驱散成功给低能量队友充能", "伤害低但命中高", "驱散失败转能量削减", "无增益敌人时输出很低"),
    _hero("huidun", "辉盾", "抗暴 / 保护 / 反制", _stats(1320, 82, 92, .07, .03, .07), ["anti_crit", "shield", "protect"], _dmg("huidun_normal", "护击", .80, extra=[{"op": "shield", "target": "lowest_ally", "max_hp_ratio": .03}]), _skill("huidun_active", "辉光堡垒", "全队护盾并降低低血队友暴击伤害", [{"op": "shield", "target": "allies", "max_hp_ratio": .05}, {"op": "modify_stat", "target": "lowest_allies", "stat": "CRIT_DAMAGE_REDUCTION", "amount": .35, "duration": 2}], "shield"), "队友受到暴击后补盾", "受到非暴击伤害获得能量", "队友被暴击至低血时提供大护盾", "面对持续伤害和非暴击阵容收益下降"),
    _hero("shashi", "砂时", "阵位调度 / 节奏", _stats(890, 84, 59, .10, .07, .06), ["formation", "tempo", "energy"], _dmg("shashi_normal", "沙刻", .76), _skill("shashi_active", "错拍", "微调己方行动位置并降低敌方能量收益", [{"op": "shift_action_order", "target": "latest_ally", "steps": -1}, {"op": "modify_energy_gain", "target": "highest_energy_enemy", "amount": -.30, "duration": 1}], "energy_wave"), "记录攻击目标行动序号", "4/5号位第一次主动消耗降低", "不能跨过1号位或改变交替规则", "伤害低，需要理解阵位节奏"),
    _hero("jinglan", "镜澜", "复制增益 / 反射 / 适应", _stats(950, 94, 58, .09, .07, .08), ["copy", "reflect", "support"], _dmg("jinglan_normal", "镜锋", .75, extra=[{"op": "copy_buff", "target": "enemy_shortest_buff", "count": 1}]), _skill("jinglan_active", "镜返", "复制队友增益并给予一次反射保护", [{"op": "copy_buff", "target": "most_buffed_ally", "count": 2, "to": "lowest_ally"}, {"op": "apply_status", "target": "lowest_ally", "status": "mirror_guard", "stacks": 1}], "shield"), "不可复制无敌、复活、觉醒和阵势", "镜护触发后恢复能量", "复制两种增益时额外净化", "队伍无增益时能力较弱"),
    _hero("lan_ying", "兰荧", "毒系治疗 / 风险管理", _stats(1020, 90, 62, .08, .05, .06), ["poison", "heal", "risk"], _dmg("lan_ying_normal", "药针", .72, extra=[{"op": "chance", "probability": .40, "then": {"op": "apply_status", "status": "poison", "stacks": 1}}]), _skill("lan_ying_active", "逆向药理", "按敌方中毒层数强化治疗并转换毒层为护盾", [{"op": "heal", "target": "lowest_allies", "power": 1.0}, {"op": "consume_status_to_shield", "status": "poison", "target": "highest_poison_enemy", "to": "lowest_ally"}], "heal"), "毒结算时治疗低血队友", "自身中毒抗性提高但非免疫", "毒层转护盾时不降低持续时间", "依赖敌方中毒，爆发治疗不如纯治疗"),
    _hero("jiqiao7", "机巧七号", "无人机 / 削盾 / 持久输出", _stats(980, 102, 64, .11, .06, .08), ["drone", "shield_break", "sustain"], _dmg("jiqiao7_normal", "无人机射击", .85), _skill("jiqiao7_active", "部署蜂群", "召唤两架临时无人机", [{"op": "summon", "unit": "drone", "count": 2, "duration": 2}, {"op": "direct_damage", "target": "shielded_enemy", "multiplier": .35}], "projectile"), "无人机不占位且不可单独攻击", "硬控时无人机本轮停止", "无人机消失时恢复能量，击破护盾额外恢复", "怕控制，爆发慢"),
    _hero("leiyuan", "雷丸", "暴击连锁 / 随机弹跳", _stats(900, 110, 50, .08, .09, .22), ["critical", "chain", "random"], _dmg("leiyuan_normal", "电击", .80, "random_enemy", "lightning", [{"op": "modify_stat", "target": "self", "stat": "CRIT_RATE", "amount": .10}]), _skill("leiyuan_active", "雷丸弹跳", "在敌人间弹跳三次", [{"op": "chain", "count": 3, "multiplier": .80, "max_hits_per_target": 2}, {"op": "conditional", "when": "critical_last_hit", "then": {"op": "force_hit_next"}}], "lightning"), "连续未暴击后提高暴击率", "暴击恢复能量，每轮最多三次", "至少暴击一次后最后一跳必定命中", "抗暴和高防御会让输出不稳定"),
    _hero("zhiyuan", "纸鸢", "标记 / 后排点杀 / 集火", _stats(920, 114, 51, .09, .08, .15), ["mark", "focus", "ranged"], _dmg("zhiyuan_normal", "鸢羽", .84, "last_enemy", "projectile", [{"op": "add_mark", "target": "hit_targets", "mark": "kite_mark", "amount": 1}]), _skill("zhiyuan_active", "落纸成锋", "消耗标记集火目标", [{"op": "direct_damage", "target": "most_marked_enemy", "multiplier": 1.60}, {"op": "consume_mark_damage", "mark": "kite_mark", "per_stack": .18}, {"op": "conditional", "when": "consumed_3_marks", "then": {"op": "focus_target_next_ally"}}], "projectile"), "队友攻击标记目标概率追加标记", "标记可驱散但不是负面控制", "目标死亡后标记转移", "需要集火时间，频繁驱散会降低收益"),
    _hero("xuanjia", "玄甲", "反击 / 受击积累 / 前排坦", _stats(1360, 100, 96, .07, .02, .08), ["counter", "tank", "retaliation"], _dmg("xuanjia_normal", "链刃", .85, extra=[{"op": "add_mark", "target": "self", "mark": "war_trace", "amount": 1}]), _skill("xuanjia_active", "玄甲回锋", "获得减伤并反击直接攻击", [{"op": "modify_stat", "target": "self", "stat": "DAMAGE_REDUCTION", "amount": .20, "duration": 1}, {"op": "counter", "target": "direct_attackers", "multiplier": .45, "max_triggers": 4}], "counter"), "战痕提升反击伤害，最多五层", "范围持续伤害不会触发反击", "触发三次以上反击后横排追加伤害", "怕持续伤害、控制和不攻击他的阵容"),
    _hero("lingxi", "灵汐", "抗控 / 保护核心 / 失败转能量", _stats(1000, 82, 66, .10, .06, .05), ["control_resist", "protect", "energy"], _dmg("lingxi_normal", "潮痕", .72, extra=[{"op": "modify_stat", "target": "lowest_resist_ally", "stat": "CONTROL_RESIST", "amount": .08}]), _skill("lingxi_active", "潮汐静域", "为高能量队友提供一次控制保护", [{"op": "apply_status", "target": "highest_energy_allies", "status": "control_guard", "stacks": 1, "count": 3}, {"op": "conditional", "when": "guarded_control_success", "then": {"op": "modify_energy", "target": "guarded_target", "amount": 20}}], "shield"), "硬控解除时恢复能量", "控制保护不可叠加", "队友连续两次硬控时首次清除", "无控制阵容下作用有限，治疗弱"),
    _hero("heluo", "赫罗", "高风险暴击 / 生命换爆发", _stats(880, 136, 46, .09, .07, .20, 1.65), ["critical", "risk", "burst"], _dmg("heluo_normal", "赤弹", 1.0, extra=[{"op": "self_damage_on_critical", "max_hp_ratio": .02}]), _skill("heluo_active", "孤注", "高倍率暴击赌博", [{"op": "direct_damage", "target": "front", "multiplier": 2.20}, {"op": "modify_stat", "target": "self", "stat": "CRIT_RATE", "amount": .25}, {"op": "conditional", "when": "not_critical", "then": {"op": "modify_energy", "target": "self", "amount": 20}}, {"op": "self_damage", "max_hp_ratio": .05}], "single_slash"), "生命越低暴击伤害越高，上限20%", "技能生命消耗最低保留1点", "低血首次主动暴击后获得护盾", "容易被持续伤害收割，需要保护"),
    _hero("mianxing", "眠星", "命中干扰 / 虚弱辅助", _stats(910, 84, 54, .11, .10, .07), ["accuracy_down", "weaken", "support"], _dmg("mianxing_normal", "星尘", .70, extra=[{"op": "modify_stat", "target": "front", "stat": "HIT", "amount": -.05, "duration": 1}]), _skill("mianxing_active", "失焦星幕", "横排降低命中并削减已行动目标能量", [{"op": "direct_damage", "target": "row", "multiplier": .65}, {"op": "modify_stat", "target": "row", "stat": "HIT", "amount": -.12, "duration": 1}, {"op": "modify_energy", "target": "acted_targets", "amount": -10}], "ice"), "敌方未命中时给低血角色护盾", "命中降低不突破系统下限", "命中三名目标后给高闪避角色增益", "不提供硬控，高命中敌人下效果下降"),
    _hero("naipaolong", "奶泡龙", "鼓舞 / 抗暴 / 残局辅助", _stats(1180, 68, 70, .07, .06, .04), ["inspire", "shield", "anti_crit"], _dmg("naipaolong_normal", "泡泡", .60, extra=[{"op": "heal", "target": "lowest_ally", "power": .10}]), _skill("naipaolong_active", "泡泡鼓舞", "全队护盾、低血充能和命中鼓舞", [{"op": "shield", "target": "allies", "max_hp_ratio": .04}, {"op": "modify_energy", "target": "below_half_allies", "amount": 10}, {"op": "modify_stat", "target": "not_acted_allies", "stat": "HIT", "amount": .08}], "heal"), "己方首次受到暴击时补盾", "自身直接伤害降低", "护盾破裂后恢复能量", "没有爆发和控制，需要搭配核心"),
]

# Every role has a readable combat vocabulary. These names are also used by
# the battle replay so a passive or awakening never falls back to a generic
# label such as “核心被动”.
HERO_NAMING = {
    "jinxiao": ("余烬回响", "断线猎火", "赤线觉醒"),
    "chengyin": ("潮汐和弦", "澄明余音", "静海回生"),
    "yaye": ("夜行刻印", "鸦影回锋", "无月终幕"),
    "leishan": ("山脉意志", "磐石壁垒", "地脉觉醒"),
    "xianlv": ("弦上共鸣", "终止和弦", "星弦觉醒"),
    "gulu": ("孢子护幕", "菌潮扩散", "王冠绽放"),
    "shuangli": ("霜痕", "寒潮三叠", "极夜霜华"),
    "tuantuan": ("柔软守望", "滚滚护航", "团圆结界"),
    "zhenchui": ("震荡蓄力", "地鸣重锤", "裂地觉醒"),
    "yaoge": ("回声集结", "终曲鼓舞", "曜歌天穹"),
    "xueji": ("棘刺回响", "血色收割", "荆冠觉醒"),
    "mochai": ("墨痕封锁", "禁令落款", "无声法庭"),
    "huidun": ("辉光护体", "圣盾裁决", "不破誓约"),
    "shashi": ("沙漏余刻", "错拍回环", "时间断面"),
    "jinglan": ("镜面映射", "返照成双", "万象归镜"),
    "lan_ying": ("兰息", "逆向药理", "青荧转生"),
    "jiqiao7": ("机芯迭代", "蜂群部署", "七号协议"),
    "leiyuan": ("电荷积累", "雷环跳跃", "万雷归一"),
    "zhiyuan": ("纸鸢留痕", "落线成锋", "天际猎签"),
    "xuanjia": ("玄甲蓄势", "回铠反震", "山河不动"),
    "lingxi": ("灵潮感知", "潮汐静域", "心灯不灭"),
    "heluo": ("赤命赌局", "孤注坠星", "血契终章"),
    "mianxing": ("星尘扰动", "失焦星幕", "梦境闭环"),
    "naipaolong": ("泡沫节拍", "泡泡鼓舞", "奶油终场"),
}

for _hero_definition in HEROES:
    _hero_id = _hero_definition["hero_id"]
    _passive_one, _passive_two, _awakening = HERO_NAMING[_hero_id]
    _hero_definition["passive_one"].update(skill_id=f"{_hero_id}_passive_one", name=_passive_one)
    _hero_definition["passive_two"].update(skill_id=f"{_hero_id}_passive_two", name=_passive_two)
    _hero_definition["awakening"].update(skill_id=f"{_hero_id}_awakening", name=_awakening)

# The starter party is deliberately readable: each active skill has a clear
# job and a different combat shape. In particular 烬绡's ultimate is a real
# row burst, not a cosmetic 1.05x normal attack.
HERO_ACTIVE_REWORKS = {
    "jinxiao": _skill("jinxiao_active", "焚界·断线", "横排爆燃并留下灼烧，随后短暂提高自身暴击率", [
        {"op": "direct_damage", "target": "row", "multiplier": 1.55},
        {"op": "apply_status", "target": "row", "status": "burn", "stacks": 1},
        {"op": "modify_stat", "target": "self", "stat": "CRIT_RATE", "amount": .10, "duration": 1},
    ], "fire"),
    "leishan": _skill("leishan_active", "磐石·回响", "为自己与相邻队友架起护盾，并短暂提升防御", [
        {"op": "shield", "target": "self_and_adjacent", "max_hp_ratio": .14},
        {"op": "modify_stat", "target": "self_and_adjacent", "stat": "DEF", "amount": .10, "duration": 2},
    ], "shield"),
    "gulu": _skill("gulu_active", "菌潮·三连孢", "对随机敌人连续播撒孢子，命中后叠加中毒", [
        {"op": "repeat", "count": 3, "effect": {"op": "direct_damage", "target": "random_enemy", "multiplier": .72}},
        {"op": "apply_status", "target": "random_enemies", "status": "poison", "stacks": 1, "count": 2},
    ], "poison"),
    "shuangli": _skill("shuangli_active", "霜华·三叠", "三次寒霜打击，最后一击有机会冻结目标", [
        {"op": "repeat", "count": 3, "effect": {"op": "direct_damage", "target": "random_enemy", "multiplier": .66}},
        {"op": "apply_status", "target": "random_enemies", "status": "chill", "stacks": 1, "count": 2},
        {"op": "chance", "probability": .30, "then": {"op": "apply_status", "target": "random_enemy", "status": "freeze", "stacks": 1}},
    ], "ice"),
    "zhenchui": _skill("zhenchui_active", "地鸣·裂阵", "重击前排并震荡相邻目标，附带短暂眩晕机会", [
        {"op": "direct_damage", "target": "front", "multiplier": 1.35},
        {"op": "direct_damage", "target": "front_adjacent", "multiplier": .65},
        {"op": "chance", "probability": .35, "then": {"op": "apply_status", "target": "front", "status": "stun", "stacks": 1}},
    ], "impact"),
}

for _hero_definition in HEROES:
    _replacement = HERO_ACTIVE_REWORKS.get(_hero_definition["hero_id"])
    if _replacement:
        _hero_definition["active_skill"] = _replacement

# A small fixed rarity catalog keeps the banner data-driven while avoiding any
# runtime rarity roll or client-controlled result.
for _index, _hero_definition in enumerate(HEROES):
    _hero_definition["rarity"] = "legendary" if _index in {2, 8, 17, 21} else ("epic" if _index % 3 == 0 else "rare")


PETS = [
    {"pet_id": "fire_fox", "name": "火狐", "passive": {"op": "on_first_active", "status": "burn", "stacks": 1}, "once_per_battle": {"op": "apply_status", "status": "burn", "stacks": 1}},
    {"pet_id": "venom_bee", "name": "毒蜂", "passive": {"op": "normal_attack_chance", "status": "poison", "probability": .25}, "once_per_battle": {"op": "modify_stat", "stat": "HIT", "amount": .10}},
    {"pet_id": "snow_owl", "name": "雪鸮", "passive": {"op": "on_first_hard_control", "modify_energy": 15}, "once_per_battle": {"op": "control_guard", "count": 1}},
    {"pet_id": "stone_turtle", "name": "石龟", "passive": {"op": "battle_start_shield", "max_hp_ratio": .06}, "once_per_battle": {"op": "damage_reduction", "amount": .12}},
    {"pet_id": "spirit_deer", "name": "灵鹿", "passive": {"op": "on_below_hp", "threshold": .40, "heal": .12}, "once_per_battle": {"op": "cleanse", "count": 1}},
    {"pet_id": "night_crow", "name": "夜鸦", "passive": {"op": "low_target_hit", "threshold": .40, "amount": .12}, "once_per_battle": {"op": "dodge", "amount": .20}},
    {"pet_id": "thunder_weasel", "name": "雷鼬", "passive": {"op": "on_control_fail", "probability": .30, "modify_energy": 12}, "once_per_battle": {"op": "force_hit_next", "count": 1}},
    {"pet_id": "mech_dog", "name": "机械犬", "passive": {"op": "energy_gain", "amount": .10}, "once_per_battle": {"op": "shield", "max_hp_ratio": .04}},
    {"pet_id": "mirror_fish", "name": "镜鱼", "passive": {"op": "buff_duration_chance", "probability": .25, "extra_turn": 1}, "once_per_battle": {"op": "copy_buff", "count": 1}},
    {"pet_id": "paper_crane", "name": "纸鹤", "passive": {"op": "marked_target_damage", "amount": .10}, "once_per_battle": {"op": "add_mark", "amount": 1}},
    {"pet_id": "bubble_fox", "name": "泡泡狸", "passive": {"op": "on_first_critical", "shield_ratio": .08}, "once_per_battle": {"op": "heal", "power": .10}},
    {"pet_id": "sand_fox", "name": "沙狐", "passive": {"op": "on_energy_reduce_refund", "ratio": .50}, "once_per_battle": {"op": "modify_energy", "amount": 20}},
]


EQUIPMENT = [
    {"equipment_id": "iron_edge", "name": "铁脉刃", "slot": "weapon", "quality": "common", "base_stats": {"ATK": 18}, "effect": {"op": "modify_stat", "stat": "ATK", "amount": 18}},
    {"equipment_id": "precision_lens", "name": "精度透镜", "slot": "core", "quality": "rare", "base_stats": {"HIT": .08}, "effect": {"op": "modify_stat", "stat": "HIT", "amount": .08}},
    {"equipment_id": "ember_coil", "name": "余烬线圈", "slot": "core", "quality": "epic", "base_stats": {"CRIT_RATE": .10}, "effect": {"op": "burn_damage", "amount": .12}},
    {"equipment_id": "guard_plate", "name": "护城甲片", "slot": "armor", "quality": "rare", "base_stats": {"DEF": 22, "HP": 110}, "effect": {"op": "shield_break_energy", "amount": 8}},
    {"equipment_id": "tide_collar", "name": "潮汐领扣", "slot": "armor", "quality": "epic", "base_stats": {"HP": 150}, "effect": {"op": "control_guard", "count": 1}},
    {"equipment_id": "venom_vial", "name": "毒相药瓶", "slot": "core", "quality": "rare", "base_stats": {"ATK": 12}, "effect": {"op": "poison_damage", "amount": .12}},
    {"equipment_id": "counter_chain", "name": "回锋链", "slot": "weapon", "quality": "epic", "base_stats": {"ATK": 25}, "effect": {"op": "counter_damage", "amount": .15}},
    {"equipment_id": "star_guard", "name": "星守晶", "slot": "armor", "quality": "legendary", "base_stats": {"HP": 190, "DEF": 30}, "effect": {"op": "critical_protection", "amount": .12}},
    {"equipment_id": "energy_battery", "name": "回能电池", "slot": "core", "quality": "common", "base_stats": {"ATK": 8}, "effect": {"op": "energy_gain", "amount": .08}},
]


ENEMIES = [
    {"enemy_id": "shield_guard", "name": "盾卫", "role": "tank", "stats": {"HP": 820, "ATK": 62, "DEF": 70, "HIT": .04, "DODGE": .02, "CRIT_RATE": .04, "CRIT_DAMAGE": 1.45}, "active": [{"op": "shield", "target": "self", "max_hp_ratio": .12}, {"op": "apply_status", "target": "front", "status": "taunt", "stacks": 1}]},
    {"enemy_id": "row_crossbow", "name": "横排弩手", "role": "ranged", "stats": {"HP": 560, "ATK": 80, "DEF": 42, "HIT": .08, "DODGE": .04, "CRIT_RATE": .07, "CRIT_DAMAGE": 1.45}, "active": [{"op": "direct_damage", "target": "row", "multiplier": .72}]},
    {"enemy_id": "rear_assassin", "name": "后排刺客", "role": "assassin", "stats": {"HP": 500, "ATK": 92, "DEF": 36, "HIT": .10, "DODGE": .10, "CRIT_RATE": .12, "CRIT_DAMAGE": 1.55}, "active": [{"op": "direct_damage", "target": "last_ally", "multiplier": 1.35}]},
    {"enemy_id": "fire_bomber", "name": "火瓶手", "role": "burn", "stats": {"HP": 580, "ATK": 74, "DEF": 40, "HIT": .06, "DODGE": .03, "CRIT_RATE": .05, "CRIT_DAMAGE": 1.45}, "active": [{"op": "direct_damage", "target": "two_random", "multiplier": .62}, {"op": "apply_status", "target": "two_random", "status": "burn", "stacks": 1}]},
    {"enemy_id": "poison_needle", "name": "毒针客", "role": "poison", "stats": {"HP": 560, "ATK": 72, "DEF": 40, "HIT": .07, "DODGE": .04, "CRIT_RATE": .04, "CRIT_DAMAGE": 1.45}, "active": [{"op": "repeat", "count": 3, "effect": {"op": "direct_damage", "target": "random_ally", "multiplier": .45}}, {"op": "apply_status", "target": "random_allies", "status": "poison", "stacks": 1}]},
    {"enemy_id": "ice_caster", "name": "冰术师", "role": "control", "stats": {"HP": 540, "ATK": 76, "DEF": 38, "HIT": .08, "DODGE": .04, "CRIT_RATE": .05, "CRIT_DAMAGE": 1.45}, "active": [{"op": "direct_damage", "target": "row", "multiplier": .64}, {"op": "chance", "probability": .14, "then": {"op": "apply_status", "status": "freeze", "stacks": 1}}]},
    {"enemy_id": "shock_trooper", "name": "震击兵", "role": "stun", "stats": {"HP": 720, "ATK": 70, "DEF": 56, "HIT": .08, "DODGE": .03, "CRIT_RATE": .05, "CRIT_DAMAGE": 1.45}, "active": [{"op": "direct_damage", "target": "front", "multiplier": .90}, {"op": "chance", "probability": .16, "then": {"op": "apply_status", "status": "stun", "stacks": 1}}]},
    {"enemy_id": "field_medic", "name": "军医", "role": "healer", "stats": {"HP": 600, "ATK": 55, "DEF": 48, "HIT": .05, "DODGE": .03, "CRIT_RATE": .03, "CRIT_DAMAGE": 1.4}, "active": [{"op": "heal", "target": "lowest_allies", "power": .80, "count": 2}]},
    {"enemy_id": "cleanser", "name": "净化师", "role": "cleanse", "stats": {"HP": 620, "ATK": 58, "DEF": 50, "HIT": .06, "DODGE": .03, "CRIT_RATE": .03, "CRIT_DAMAGE": 1.4}, "active": [{"op": "cleanse", "target": "allies", "count": 1}]},
    {"enemy_id": "drummer", "name": "鼓手", "role": "energy", "stats": {"HP": 580, "ATK": 60, "DEF": 42, "HIT": .06, "DODGE": .04, "CRIT_RATE": .04, "CRIT_DAMAGE": 1.4}, "active": [{"op": "modify_energy", "target": "lowest_allies", "amount": 18, "count": 2}]},
    {"enemy_id": "counter_guard", "name": "反击卫", "role": "counter", "stats": {"HP": 760, "ATK": 65, "DEF": 64, "HIT": .05, "DODGE": .02, "CRIT_RATE": .04, "CRIT_DAMAGE": 1.4}, "active": [{"op": "modify_stat", "target": "self", "stat": "DAMAGE_REDUCTION", "amount": .16, "duration": 1}, {"op": "counter", "target": "direct_attackers", "multiplier": .35, "max_triggers": 3}]},
    {"enemy_id": "bomb_golem", "name": "爆破傀儡", "role": "burst", "stats": {"HP": 850, "ATK": 72, "DEF": 58, "HIT": .05, "DODGE": .01, "CRIT_RATE": .03, "CRIT_DAMAGE": 1.4}, "active": [{"op": "direct_damage", "target": "front", "multiplier": 1.0}], "on_death": [{"op": "direct_damage", "target": "adjacent", "multiplier": .50}]},
]


BOSSES = [
    {"boss_id": "twin_aegis", "name": "双盾守门者", "mechanic": "dual_shield", "strategies": ["驱散并削盾", "持续伤害", "集中火力后排"]},
    {"boss_id": "summon_mother", "name": "召群母体", "mechanic": "summons", "strategies": ["先处理召唤物", "群体爆发", "控制召唤节奏"]},
    {"boss_id": "redline_beast", "name": "红线兽", "mechanic": "low_hp_enrage", "strategies": ["护盾与治疗", "标记收割", "控制爆发窗口"]},
    {"boss_id": "purge_clock", "name": "净环钟", "mechanic": "periodic_cleanse", "strategies": ["单次叠层引爆", "净化后补伤", "不依赖状态的输出"]},
    {"boss_id": "null_battery", "name": "空能电池", "mechanic": "energy_suppression", "strategies": ["普攻充能", "能量保护", "低能量爆发"]},
    {"boss_id": "healing_core", "name": "复原核心", "mechanic": "healing_core", "strategies": ["驱散减疗", "集火治疗者", "持续伤害压制"]},
    {"boss_id": "rear_vault", "name": "后卫穹顶", "mechanic": "rear_protection", "strategies": ["横排攻击", "标记转移", "前排牵制"]},
    {"boss_id": "shield_furnace", "name": "护盾炉心", "mechanic": "shield_burst", "strategies": ["削盾无人机", "等盾破反击", "毒伤绕盾"]},
    {"boss_id": "reversal_guard", "name": "回锋将", "mechanic": "counter_phase", "strategies": ["使用持续伤害", "低频重击", "护盾后反击"]},
    {"boss_id": "mixed_herald", "name": "混态使者", "mechanic": "mixed_status", "strategies": ["净化与驱散", "抗控保护", "多种状态联动"]},
]


STANCES = {
    "balanced": {"label": "均衡", "initiative_modifier": 0.0},
    "vanguard": {"label": "先遣", "initiative_modifier": 0.06, "first_round_damage_taken": 0.05},
    "fortify": {"label": "固守", "initiative_modifier": -0.06, "first_round_shield": .05},
}


STATUS_RULES = {
    "burn": {"max_stacks": 4, "duration": 2, "tick_ratio": .25, "trigger": "turn_end"},
    "poison": {"max_stacks": 6, "duration": 3, "tick_ratio": .18, "trigger": "turn_end", "boss_cap": .08},
    "bleed": {"max_stacks": 4, "duration": 2, "tick_ratio": .22, "trigger": "after_attack"},
    "chill": {"max_stacks": 3, "duration": 1, "hit_reduction": .05},
    "armor_break": {"max_stacks": 2, "duration": 2, "def_reduction": .10},
    "anti_heal": {"max_stacks": 1, "duration": 2, "heal_reduction": .25},
    "freeze": {"max_stacks": 1, "duration": 1, "hard_control": True},
    "stun": {"max_stacks": 1, "duration": 1, "hard_control": True},
    "silence": {"max_stacks": 1, "duration": 1},
    "resolve": {"max_stacks": 1, "duration": 1, "control_reduction": .50},
    "taunt": {"max_stacks": 1, "duration": 1},
}
