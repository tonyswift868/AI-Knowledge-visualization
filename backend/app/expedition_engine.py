"""Deterministic, server-authoritative battle simulation for Learning Expedition.

The engine intentionally accepts plain data and emits an append-only event list.
It contains a small declarative interpreter for the reviewed effect operations in
expedition_data.py; it never evaluates configuration as code.
"""
from __future__ import annotations

import hashlib
import random
from copy import deepcopy
from typing import Any

from .expedition_data import ENEMIES, HEROES, STATUS_RULES, STANCES

EVENT_TYPES = {
    "BATTLE_START", "INITIATIVE_ROLL", "ROUND_START", "TURN_START", "NORMAL_ATTACK",
    "SKILL_CAST", "DAMAGE", "CRITICAL", "MISS", "DODGE", "HEAL", "SHIELD_GAIN",
    "SHIELD_ABSORB", "SHIELD_BREAK", "STATUS_APPLY", "STATUS_TICK", "STATUS_REMOVE",
    "CLEANSE", "DISPEL", "ENERGY_CHANGE", "COUNTER", "TAUNT", "UNIT_DEATH",
    "ROUND_END", "BATTLE_END",
}

# 战斗展示节奏：远征关卡是“每天一小回合”的剧情体验，不应变成数十回合
# 的血条消耗战。统一压缩双方生命池，并略微放大有效伤害，让每次命中都
# 能在立绘战场上形成可感知的反馈；护盾、治疗和持续伤害仍然按缩放后的
# 最大生命值计算，因此事件回放与最终单位状态保持一致。
COMBAT_HP_SCALE = 0.64
COMBAT_DAMAGE_SCALE = 1.12
DIFFICULTY_VERSION = 3

ENEMY_SKILL_NAMES = {
    "row_crossbow": "破阵·横弩齐射",
    "poison_needle": "蚀骨·三叠毒针",
    "field_medic": "军阵·急救号令",
    "counter_guard": "铁壁·反守为攻",
    "bomb_golem": "震爆·重槌落地",
}


def stable_seed(*parts: Any) -> int:
    raw = "|".join(str(item) for item in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") & 0x7FFFFFFF


def stage_difficulty(stage_number: int) -> dict[str, float]:
    """Shared progression curve used by the API and offline balance runner.

    Regular stages rise gradually.  Every tenth stage adds a visible step;
    chapter and milestone bosses add bounded multipliers rather than a hidden
    one-off rule, so the same curve can be audited from stage 1 to 300.
    """
    number = max(1, int(stage_number))
    # One training level is intended to cover roughly five stages.  The old
    # curve only made stage 31 about three percent stronger than stage 1, so
    # progression was visually infinite but mechanically flat.
    tier = (number - 1) / 5.0
    boss_factor = 1.10 if number % 10 == 0 else 1.0
    if number % 50 == 0:
        boss_factor *= 1.12
    if number % 100 == 0:
        boss_factor *= 1.15
    return {
        "hp": round((1.0 + 0.20 * tier) * boss_factor, 4),
        "atk": round((1.0 + 0.32 * tier) * boss_factor, 4),
        "defense": round((1.0 + 0.10 * tier) * boss_factor, 4),
        "hit": round(min(0.12, 0.004 * tier), 4),
        "dodge": round(min(0.08, 0.0025 * tier), 4),
    }


def stage_enemy_ids(stage_number: int) -> list[str]:
    """Keep the comparison roster stable; difficulty comes from audited rules."""
    return ["row_crossbow", "poison_needle", "field_medic", "counter_guard", "bomb_golem"]


def _cell_coordinates(cell_id: str | None, fallback_position: int) -> tuple[str, int, int]:
    """Return the stable 3x3 cell identity used by formation and battle views.

    ``cell_0`` is the player's front/right cell.  The renderer mirrors the
    x-axis for the enemy side, while the engine keeps both sides in their own
    point of view so target selection can reason about rows and front/back.
    """
    candidate = str(cell_id or "")
    try:
        index = int(candidate[5:] if candidate.startswith("cell_") else candidate)
    except (TypeError, ValueError):
        index = max(0, int(fallback_position) - 1)
    index = max(0, min(8, index))
    return f"cell_{index}", index // 3, index % 3


class BattleEngine:
    def __init__(self, *, seed: int, heroes: list[dict[str, Any]], enemy_ids: list[str], stance: str = "balanced", difficulty: dict[str, float] | None = None, training_level: int = 1):
        self.seed = int(seed)
        self.rng = random.Random(self.seed)
        self.events: list[dict[str, Any]] = []
        self.index = 0
        self.time = 0
        self.stance = stance if stance in STANCES else "balanced"
        self.difficulty = {"hp": 1.0, "atk": 1.0, "defense": 1.0, "hit": 0.0, "dodge": 0.0, **(difficulty or {})}
        self.training_level = max(1, min(100, int(training_level)))
        hero_by_id = {h["hero_id"]: h for h in HEROES}
        self.allies = [self._unit(f"hero:{h['hero_id']}", h, "ally", i + 1) for i, h in enumerate(heroes) if h.get("hero_id") in hero_by_id]
        self.enemies = [self._enemy_unit(f"enemy:{i}:{enemy_id}", enemy_id, i + 1, f"cell_{i}") for i, enemy_id in enumerate(enemy_ids)]
        self.units = self.allies + self.enemies

    def _unit(self, uid: str, definition: dict[str, Any], side: str, position: int) -> dict[str, Any]:
        stats = deepcopy(definition["base_stats"])
        level_steps = self.training_level - 1
        # Training must remain a meaningful answer to the steeper expedition
        # curve, including milestone stages.  Level 1 is intentionally
        # outmatched by stage 31, while its recommended level can clear it.
        stats["ATK"] = int(float(stats.get("ATK", 0)) * (1.0 + 0.15 * level_steps))
        stats["DEF"] = int(float(stats.get("DEF", 0)) * (1.0 + 0.10 * level_steps))
        stats["HP"] = int(float(stats.get("HP", 1)) * (1.0 + 0.15 * level_steps))
        stats["HP"] = max(1, int(stats["HP"] * COMBAT_HP_SCALE))
        cell_id, grid_row, grid_col = _cell_coordinates(definition.get("cell_id"), position)
        return {"id": uid, "hero_id": definition["hero_id"], "name": definition["name"], "side": side, "position": position, "cell_id": cell_id, "grid_row": grid_row, "grid_col": grid_col, "stats": stats, "max_hp": int(stats["HP"]), "hp": int(stats["HP"]), "energy": 0, "shield": 0, "statuses": {}, "alive": True, "definition": definition, "damage": 0, "healing": 0, "round_hit_energy": 0}

    def _enemy_unit(self, uid: str, enemy_id: str, position: int, cell_id: str | None = None) -> dict[str, Any]:
        definition = next((item for item in ENEMIES if item["enemy_id"] == enemy_id), ENEMIES[0])
        definition = deepcopy(definition)
        definition["normal_attack"] = {"skill_id": f"{enemy_id}_normal", "name": "基础攻击", "effects": [{"op": "direct_damage", "target": "front", "multiplier": .72}], "animation": "enemy_attack"}
        definition["active_skill"] = {"skill_id": f"{enemy_id}_active", "name": ENEMY_SKILL_NAMES.get(enemy_id, "敌技·破阵"), "effects": definition.get("active", []), "animation": "enemy_skill"}
        definition["base_stats"] = definition["stats"]
        combat_stats = deepcopy(definition["stats"])
        # Keep normal training stages readable: roughly 10-18 rounds for a
        # prepared party, with late milestones allowed to approach 30. The old
        # 2.1x/1.8x envelope made early enemies spongey and late stages swingy.
        combat_stats["HP"] = max(1, int(combat_stats["HP"] * 1.35 * float(self.difficulty["hp"]) * COMBAT_HP_SCALE))
        combat_stats["ATK"] = int(combat_stats["ATK"] * 1.20 * float(self.difficulty["atk"]))
        combat_stats["DEF"] = int(combat_stats["DEF"] * 1.15 * float(self.difficulty["defense"]))
        combat_stats["HIT"] = float(combat_stats.get("HIT", 0)) + float(self.difficulty.get("hit", 0))
        combat_stats["DODGE"] = float(combat_stats.get("DODGE", 0)) + float(self.difficulty.get("dodge", 0))
        cell_id, grid_row, grid_col = _cell_coordinates(cell_id, position)
        return {"id": uid, "hero_id": enemy_id, "name": definition["name"], "side": "enemy", "position": position, "cell_id": cell_id, "grid_row": grid_row, "grid_col": grid_col, "stats": combat_stats, "max_hp": int(combat_stats["HP"]), "hp": int(combat_stats["HP"]), "energy": 0, "shield": 0, "statuses": {}, "alive": True, "definition": definition, "damage": 0, "healing": 0, "round_hit_energy": 0}

    def emit(self, event_type: str, actor: dict[str, Any] | None = None, targets: list[dict[str, Any]] | None = None, *, skill: str = "", numeric: dict[str, Any] | None = None, statuses: list[str] | None = None, critical: bool = False, miss: bool = False, hint: str = "") -> None:
        if event_type not in EVENT_TYPES:
            return
        self.events.append({"event_index": self.index, "event_type": event_type, "actor_id": actor["id"] if actor else "", "target_ids": [u["id"] for u in (targets or [])], "source_skill_id": skill, "numeric_values": numeric or {}, "status_ids": statuses or [], "is_critical": critical, "is_miss": miss, "animation_hint": hint or event_type.lower(), "timestamp_offset": self.time, "rng_trace": {"seed": self.seed, "event_index": self.index}})
        self.index += 1

    def alive(self, side: str) -> list[dict[str, Any]]:
        return [u for u in self.units if u["side"] == side and u["alive"]]

    def _front_order(self, actor: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Order targets by the shared row/front geometry, never by list order."""
        return sorted(candidates, key=lambda unit: (
            abs(int(unit["grid_row"]) - int(actor["grid_row"])),
            int(unit["grid_col"]),
            int(unit["position"]),
        ))

    def _same_row(self, actor: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [unit for unit in candidates if int(unit["grid_row"]) == int(actor["grid_row"])]

    def _target(self, actor: dict[str, Any], selector: str, count: int = 1) -> list[dict[str, Any]]:
        pool = self.alive("enemy" if actor["side"] == "ally" else "ally")
        allies = self.alive(actor["side"])
        selector = selector or "front"
        if selector in {"self", "self_and_adjacent"}: return [actor] + ([u for u in allies if u is not actor][:2] if selector.endswith("adjacent") else [])
        if not pool and selector not in {"allies", "lowest_allies", "below_half_allies"}: return []
        if selector in {"all_enemies", "all_allies", "allies", "random_enemies", "random_allies"}:
            selected = pool if "enemy" in selector else allies
            if "random" in selector:
                # Randomness should stay inside the actor's row when possible;
                # otherwise a ranged unit can visually attack across the board.
                selected = self._same_row(actor, selected) or selected
                self.rng.shuffle(selected)
            return selected[:count] if "random" in selector and count else selected
        if "lowest" in selector:
            selected = sorted(allies if "ally" in selector else pool, key=lambda u: u["hp"] / max(1, u["max_hp"]))
            return selected[:count]
        if "last" in selector or "rear" in selector:
            same_row = self._same_row(actor, pool)
            ordered = sorted(same_row or pool, key=lambda u: (-int(u["grid_col"]), int(u["position"])))
            return ordered[:count]
        if selector == "random_enemy" or selector == "random_ally":
            selected = pool if "enemy" in selector else allies
            selected = self._same_row(actor, selected) or selected
            return [self.rng.choice(selected)] if selected else []
        if selector == "row" or selector == "front_adjacent":
            same_row = self._same_row(actor, pool)
            if selector == "row":
                return sorted(same_row, key=lambda u: (int(u["grid_col"]), int(u["position"])))[:3]
            ordered = self._front_order(actor, pool)
            return ordered[:2]
        if selector == "all_enemies": return pool
        same_row = self._same_row(actor, pool)
        ordered = self._front_order(actor, same_row or pool)
        return ordered[:count]

    def _status(self, unit: dict[str, Any], status: str, stacks: int = 1, duration: int | None = None) -> None:
        rule = STATUS_RULES.get(status, {"max_stacks": 3, "duration": 1})
        current = unit["statuses"].get(status, {"stacks": 0, "duration": 0})
        current["stacks"] = min(int(rule.get("max_stacks", 3)), int(current.get("stacks", 0)) + stacks)
        current["duration"] = max(int(current.get("duration", 0)), int(duration or rule.get("duration", 1)))
        unit["statuses"][status] = current

    def _change_energy(self, unit: dict[str, Any], amount: int) -> None:
        before = unit["energy"]
        unit["energy"] = max(0, min(150, before + int(amount)))
        self.emit("ENERGY_CHANGE", unit, numeric={"before": before, "after": unit["energy"], "delta": unit["energy"] - before})

    def _damage(self, actor: dict[str, Any], target: dict[str, Any], multiplier: float, skill: str = "", *, allow_counter: bool = True) -> None:
        if not target["alive"]: return
        a = actor["stats"]; t = target["stats"]
        hit = max(.50, min(1.0, .90 + float(a.get("HIT", 0)) - float(t.get("DODGE", 0)) - .05 * target["statuses"].get("chill", {}).get("stacks", 0)))
        if self.rng.random() > hit:
            self.emit("DODGE", actor, [target], skill=skill, miss=True, hint="dodge")
            self.emit("MISS", actor, [target], skill=skill, miss=True, hint="miss")
            return
        crit = self.rng.random() < max(0, min(.75, float(a.get("CRIT_RATE", 0))))
        defense = max(0, float(t.get("DEF", 0)))
        raw = float(a.get("ATK", 0)) * float(multiplier) * (100 / (100 + defense)) * self.rng.uniform(.95, 1.05)
        if crit: raw *= float(a.get("CRIT_DAMAGE", 1.5))
        reduction = max(0.0, min(0.80, float(t.get("DAMAGE_REDUCTION", 0))))
        amount = max(1, int(raw * COMBAT_DAMAGE_SCALE * (1.0 - reduction)))
        absorbed = min(target["shield"], amount)
        if absorbed:
            target["shield"] -= absorbed; amount -= absorbed; self.emit("SHIELD_ABSORB", actor, [target], skill=skill, numeric={"amount": absorbed}, hint="shield")
            if target["shield"] == 0: self.emit("SHIELD_BREAK", actor, [target], skill=skill, hint="shield_break")
        if amount:
            target["hp"] = max(0, target["hp"] - amount); actor["damage"] += amount
            incoming = min(8, max(0, 24 - int(target.get("round_hit_energy", 0)))); target["round_hit_energy"] = int(target.get("round_hit_energy", 0)) + incoming
            if incoming: self._change_energy(target, incoming)
            if "freeze" in target["statuses"]:
                del target["statuses"]["freeze"]; self.emit("STATUS_REMOVE", actor, [target], skill=skill, statuses=["freeze"], hint="freeze_break")
        self.emit("DAMAGE", actor, [target], skill=skill, numeric={"amount": amount, "raw": int(raw), "remaining_hp": target["hp"]}, critical=crit, hint="critical" if crit else "hit")
        if crit: self.emit("CRITICAL", actor, [target], skill=skill, numeric={"amount": amount}, critical=True, hint="critical")
        if target["hp"] <= 0:
            target["alive"] = False; self.emit("UNIT_DEATH", actor, [target], skill=skill, hint="death")
        elif allow_counter and actor["alive"] and target.get("counter_state"):
            counter = target["counter_state"]
            if int(counter.get("remaining", 0)) > 0:
                counter["remaining"] = int(counter.get("remaining", 0)) - 1
                self.emit("COUNTER", target, [actor], skill="counter", numeric={"multiplier": float(counter.get("multiplier", .35))}, hint="counter")
                self._damage(target, actor, float(counter.get("multiplier", .35)), "counter", allow_counter=False)

    def _heal(self, actor: dict[str, Any], target: dict[str, Any], power: float) -> None:
        if not target["alive"]: return
        amount = min(target["max_hp"] - target["hp"], max(1, int(actor["stats"].get("ATK", 0) * power)))
        target["hp"] += amount; actor["healing"] += amount
        self.emit("HEAL", actor, [target], numeric={"amount": amount, "remaining_hp": target["hp"]}, hint="heal")

    def _apply_effect(self, actor: dict[str, Any], effect: dict[str, Any], skill: str, context: dict[str, Any] | None = None) -> None:
        op = effect.get("op"); context = context or {}
        if op == "chance":
            if self.rng.random() <= float(effect.get("probability", 0)): self._apply_effect(actor, effect.get("then", {}), skill, context)
            return
        if op == "conditional":
            conditions = {"target_below_30": any(u["hp"] / max(1, u["max_hp"]) < .30 for u in context.get("targets", [])), "three_debuffs": any(len(u["statuses"]) >= 3 for u in context.get("targets", []))}
            if conditions.get(str(effect.get("when")), False): self._apply_effect(actor, effect.get("then", {}), skill, context)
            return
        if op == "repeat":
            for _ in range(min(8, int(effect.get("count", 1)))): self._apply_effect(actor, effect.get("effect", {}), skill, context)
            return
        targets = self._target(actor, str(effect.get("target") or "front"), int(effect.get("count", 1)))
        if op == "direct_damage":
            for target in targets: self._damage(actor, target, float(effect.get("multiplier", .5)), skill)
        elif op == "chain":
            for _ in range(min(6, int(effect.get("count", 1)))):
                targets = self._target(actor, "random_enemy", 1)
                for target in targets: self._damage(actor, target, float(effect.get("multiplier", .5)), skill)
        elif op == "heal":
            for target in targets: self._heal(actor, target, float(effect.get("power", .25)))
        elif op == "shield":
            for target in targets:
                amount = max(1, int(target["max_hp"] * float(effect.get("max_hp_ratio", .05))))
                target["shield"] += amount; self.emit("SHIELD_GAIN", actor, [target], skill=skill, numeric={"amount": amount}, hint="shield")
        elif op == "apply_status":
            status = str(effect.get("status", ""))
            for target in targets:
                self._status(target, status, int(effect.get("stacks", 1)), effect.get("duration")); self.emit("STATUS_APPLY", actor, [target], skill=skill, statuses=[status], numeric={"stacks": target["statuses"][status]["stacks"]}, hint="status")
        elif op == "modify_energy":
            for target in targets: self._change_energy(target, int(effect.get("amount", 0)))
        elif op == "modify_stat":
            stat = str(effect.get("stat", "")); amount = float(effect.get("amount", 0))
            for target in targets:
                target["stats"][stat] = float(target["stats"].get(stat, 0)) + amount
                target.setdefault("temporary_modifiers", []).append({"stat": stat, "amount": amount, "duration": max(1, int(effect.get("duration", 1))) + 1})
                self.emit("STATUS_APPLY", actor, [target], skill=skill, statuses=[stat.lower()], numeric={"amount": amount}, hint="buff")
        elif op == "counter":
            actor["counter_state"] = {"multiplier": float(effect.get("multiplier", .35)), "remaining": max(1, int(effect.get("max_triggers", 1))), "duration": max(1, int(effect.get("duration", 1))) + 1}
            self.emit("STATUS_APPLY", actor, [actor], skill=skill, statuses=["counter_ready"], numeric={"max_triggers": actor["counter_state"]["remaining"]}, hint="counter_ready")
        elif op in {"cleanse", "remove_status"}:
            for target in targets:
                removed = list(target["statuses"].keys())[:int(effect.get("count", 1))] if op == "cleanse" else [str(effect.get("status"))]
                for status in removed:
                    if status in target["statuses"]: del target["statuses"][status]; self.emit("CLEANSE" if op == "cleanse" else "STATUS_REMOVE", actor, [target], skill=skill, statuses=[status], hint="cleanse")
        elif op == "dispel_buff":
            for target in targets: self.emit("DISPEL", actor, [target], skill=skill, hint="dispel")

    def _tick(self, unit: dict[str, Any]) -> None:
        for status, value in list(unit["statuses"].items()):
            rule = STATUS_RULES.get(status, {})
            if rule.get("trigger") == "turn_end":
                amount = max(1, int(unit["max_hp"] * float(rule.get("tick_ratio", .05)) * value.get("stacks", 1)))
                unit["hp"] = max(0, unit["hp"] - amount); self.emit("STATUS_TICK", unit, [unit], statuses=[status], numeric={"amount": amount}, hint="status_tick")
            value["duration"] -= 1
            if value["duration"] <= 0: del unit["statuses"][status]; self.emit("STATUS_REMOVE", unit, [unit], statuses=[status], hint="status_remove")
            if unit["hp"] <= 0 and unit["alive"]: unit["alive"] = False; self.emit("UNIT_DEATH", unit, [unit], hint="death")
        for modifier in list(unit.get("temporary_modifiers", [])):
            modifier["duration"] = int(modifier.get("duration", 1)) - 1
            if modifier["duration"] <= 0:
                stat = str(modifier.get("stat", "")); unit["stats"][stat] = float(unit["stats"].get(stat, 0)) - float(modifier.get("amount", 0)); unit["temporary_modifiers"].remove(modifier)
                self.emit("STATUS_REMOVE", unit, [unit], statuses=[stat.lower()], hint="buff_expire")
        if unit.get("counter_state"):
            unit["counter_state"]["duration"] = int(unit["counter_state"].get("duration", 1)) - 1
            if unit["counter_state"]["duration"] <= 0:
                del unit["counter_state"]
                self.emit("STATUS_REMOVE", unit, [unit], statuses=["counter_ready"], hint="counter_expire")

    def _take_turn(self, actor: dict[str, Any]) -> None:
        if not actor["alive"]: return
        self.emit("TURN_START", actor)
        hard = actor["statuses"].get("freeze") or actor["statuses"].get("stun")
        if hard:
            status = "freeze" if actor["statuses"].get("freeze") else "stun"
            del actor["statuses"][status]; self.emit("STATUS_REMOVE", actor, [actor], statuses=[status], hint="control_resolve"); self._status(actor, "resolve", 1, 1); self.emit("STATUS_APPLY", actor, [actor], statuses=["resolve"], hint="resolve"); return
        active = actor["energy"] >= 100 and "silence" not in actor["statuses"]
        skill = actor["definition"].get("active_skill", {}) if active else actor["definition"].get("normal_attack", {})
        if active: self._change_energy(actor, -100); self.emit("SKILL_CAST", actor, skill=skill.get("skill_id", ""), hint=skill.get("animation", "skill"))
        else: self.emit("NORMAL_ATTACK", actor, skill=skill.get("skill_id", ""), hint=skill.get("animation", "attack"))
        for effect in skill.get("effects", []): self._apply_effect(actor, effect, skill.get("skill_id", ""))
        if not active: self._change_energy(actor, 50)
        self._tick(actor)

    def run(self, max_rounds: int = 30) -> dict[str, Any]:
        self.emit("BATTLE_START", hint="battle_start")
        modifier = float(STANCES.get(self.stance, {}).get("initiative_modifier", 0))
        self.emit("INITIATIVE_ROLL", numeric={"ally": round(max(.40, min(.60, .50 + modifier)), 3), "enemy": round(max(.40, min(.60, .50 - modifier)), 3)})
        for round_number in range(1, max_rounds + 1):
            if not self.alive("ally") or not self.alive("enemy"): break
            self.emit("ROUND_START", numeric={"round": round_number})
            for unit in self.units: unit["round_hit_energy"] = 0
            sides = ["ally", "enemy"] if round_number % 2 else ["enemy", "ally"]
            for side in sides:
                for actor in sorted(self.alive(side), key=lambda u: u["position"]):
                    self._take_turn(actor)
                    if not self.alive("ally") or not self.alive("enemy"): break
                if not self.alive("ally") or not self.alive("enemy"): break
            self.emit("ROUND_END", numeric={"round": round_number})
            self.time += 1000
        winner = "ally" if self.alive("ally") and not self.alive("enemy") else ("enemy" if self.alive("enemy") and not self.alive("ally") else "draw")
        self.emit("BATTLE_END", numeric={"winner": winner, "rounds": min(max_rounds, round_number if 'round_number' in locals() else 0)}, hint="victory" if winner == "ally" else "defeat")
        return {"winner": winner, "rounds": min(max_rounds, round_number if 'round_number' in locals() else 0), "seed": self.seed, "events": self.events, "units": [{"id": u["id"], "hero_id": u["hero_id"], "name": u["name"], "side": u["side"], "position": u["position"], "cell_id": u["cell_id"], "grid_row": u["grid_row"], "grid_col": u["grid_col"], "hp": u["hp"], "max_hp": u["max_hp"], "shield": u["shield"], "energy": u["energy"], "statuses": u["statuses"], "damage": u["damage"], "healing": u["healing"], "alive": u["alive"]} for u in self.units]}
