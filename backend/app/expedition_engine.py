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


def stable_seed(*parts: Any) -> int:
    raw = "|".join(str(item) for item in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") & 0x7FFFFFFF


class BattleEngine:
    def __init__(self, *, seed: int, heroes: list[dict[str, Any]], enemy_ids: list[str], stance: str = "balanced"):
        self.seed = int(seed)
        self.rng = random.Random(self.seed)
        self.events: list[dict[str, Any]] = []
        self.index = 0
        self.time = 0
        self.stance = stance if stance in STANCES else "balanced"
        hero_by_id = {h["hero_id"]: h for h in HEROES}
        self.allies = [self._unit(f"hero:{h['hero_id']}", h, "ally", i + 1) for i, h in enumerate(heroes) if h.get("hero_id") in hero_by_id]
        self.enemies = [self._enemy_unit(f"enemy:{i}:{enemy_id}", enemy_id, i + 1) for i, enemy_id in enumerate(enemy_ids)]
        self.units = self.allies + self.enemies

    def _unit(self, uid: str, definition: dict[str, Any], side: str, position: int) -> dict[str, Any]:
        stats = deepcopy(definition["base_stats"])
        return {"id": uid, "hero_id": definition["hero_id"], "name": definition["name"], "side": side, "position": position, "stats": stats, "max_hp": int(stats["HP"]), "hp": int(stats["HP"]), "energy": 0, "shield": 0, "statuses": {}, "alive": True, "definition": definition, "damage": 0, "healing": 0, "round_hit_energy": 0}

    def _enemy_unit(self, uid: str, enemy_id: str, position: int) -> dict[str, Any]:
        definition = next((item for item in ENEMIES if item["enemy_id"] == enemy_id), ENEMIES[0])
        definition = deepcopy(definition)
        definition["normal_attack"] = {"skill_id": f"{enemy_id}_normal", "name": "基础攻击", "effects": [{"op": "direct_damage", "target": "front", "multiplier": .72}], "animation": "enemy_attack"}
        definition["active_skill"] = {"skill_id": f"{enemy_id}_active", "name": "敌方技能", "effects": definition.get("active", []), "animation": "enemy_skill"}
        definition["base_stats"] = definition["stats"]
        combat_stats = deepcopy(definition["stats"])
        # The catalog values are readable baseline values; the combat pass adds
        # room for formation, equipment and future chapter scaling.
        combat_stats["HP"] = int(combat_stats["HP"] * 2.1)
        combat_stats["ATK"] = int(combat_stats["ATK"] * 1.8)
        combat_stats["DEF"] = int(combat_stats["DEF"] * 1.25)
        return {"id": uid, "hero_id": enemy_id, "name": definition["name"], "side": "enemy", "position": position, "stats": combat_stats, "max_hp": int(combat_stats["HP"]), "hp": int(combat_stats["HP"]), "energy": 0, "shield": 0, "statuses": {}, "alive": True, "definition": definition, "damage": 0, "healing": 0, "round_hit_energy": 0}

    def emit(self, event_type: str, actor: dict[str, Any] | None = None, targets: list[dict[str, Any]] | None = None, *, skill: str = "", numeric: dict[str, Any] | None = None, statuses: list[str] | None = None, critical: bool = False, miss: bool = False, hint: str = "") -> None:
        if event_type not in EVENT_TYPES:
            return
        self.events.append({"event_index": self.index, "event_type": event_type, "actor_id": actor["id"] if actor else "", "target_ids": [u["id"] for u in (targets or [])], "source_skill_id": skill, "numeric_values": numeric or {}, "status_ids": statuses or [], "is_critical": critical, "is_miss": miss, "animation_hint": hint or event_type.lower(), "timestamp_offset": self.time, "rng_trace": {"seed": self.seed, "event_index": self.index}})
        self.index += 1

    def alive(self, side: str) -> list[dict[str, Any]]:
        return [u for u in self.units if u["side"] == side and u["alive"]]

    def _target(self, actor: dict[str, Any], selector: str, count: int = 1) -> list[dict[str, Any]]:
        pool = self.alive("enemy" if actor["side"] == "ally" else "ally")
        allies = self.alive(actor["side"])
        selector = selector or "front"
        if selector in {"self", "self_and_adjacent"}: return [actor] + ([u for u in allies if u is not actor][:2] if selector.endswith("adjacent") else [])
        if not pool and selector not in {"allies", "lowest_allies", "below_half_allies"}: return []
        if selector in {"all_enemies", "all_allies", "allies", "random_enemies", "random_allies"}:
            selected = pool if "enemy" in selector else allies
            if "random" in selector: self.rng.shuffle(selected)
            return selected[:count] if "random" in selector and count else selected
        if "lowest" in selector:
            selected = sorted(allies if "ally" in selector else pool, key=lambda u: u["hp"] / max(1, u["max_hp"]))
            return selected[:count]
        if "last" in selector or "rear" in selector:
            return sorted(pool, key=lambda u: u["position"], reverse=True)[:count]
        if selector == "random_enemy" or selector == "random_ally":
            selected = pool if "enemy" in selector else allies
            return [self.rng.choice(selected)] if selected else []
        if selector == "row" or selector == "front_adjacent":
            ordered = sorted(pool, key=lambda u: u["position"])
            return ordered[:3] if selector == "row" else ordered[:2]
        if selector == "all_enemies": return pool
        return sorted(pool, key=lambda u: u["position"])[:count]

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

    def _damage(self, actor: dict[str, Any], target: dict[str, Any], multiplier: float, skill: str = "") -> None:
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
        amount = max(1, int(raw))
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
            for target in targets: target["stats"][stat] = float(target["stats"].get(stat, 0)) + amount
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
        return {"winner": winner, "rounds": min(max_rounds, round_number if 'round_number' in locals() else 0), "seed": self.seed, "events": self.events, "units": [{"id": u["id"], "name": u["name"], "side": u["side"], "hp": u["hp"], "max_hp": u["max_hp"], "damage": u["damage"], "healing": u["healing"], "alive": u["alive"]} for u in self.units]}
