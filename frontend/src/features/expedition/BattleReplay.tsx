import { useEffect, useRef, useState } from "react"
import type { BattleEvent } from "./api"

type Props = { events: BattleEvent[]; winner?: string; stageNumber?: number }
const eventLabel: Record<string, string> = { BATTLE_START: "战斗开始", INITIATIVE_ROLL: "行动顺序", ROUND_START: "回合开始", TURN_START: "轮到行动", NORMAL_ATTACK: "普通攻击", SKILL_CAST: "主动技能", DAMAGE: "造成伤害", CRITICAL: "暴击", MISS: "未命中", DODGE: "闪避", HEAL: "治疗", SHIELD_GAIN: "获得护盾", SHIELD_BREAK: "护盾破裂", STATUS_APPLY: "状态施加", STATUS_TICK: "状态结算", STATUS_REMOVE: "状态解除", CLEANSE: "净化", DISPEL: "驱散", ENERGY_CHANGE: "能量变化", COUNTER: "反击", TAUNT: "嘲讽", UNIT_DEATH: "单位退场", ROUND_END: "回合结束", BATTLE_END: "战斗结束" }

export function BattleReplay({ events, winner, stageNumber }: Props) {
  const mountRef = useRef<HTMLDivElement>(null)
  const [speed, setSpeed] = useState<1 | 2 | 4>(1)
  const [reducedMotion, setReducedMotion] = useState(false)
  const [cursor, setCursor] = useState(-1)

  useEffect(() => {
    if (!mountRef.current) return
    setCursor(-1)
    let disposed = false
    let game: any
    void import("phaser").then((module: any) => {
      if (disposed || !mountRef.current) return
      const Phaser = module.default || module
      class ExpeditionBattleScene extends Phaser.Scene {
        private replayEvents: BattleEvent[] = []
        private replaySpeed = 1
        private compactMotion = false
        private onReplayEvent?: (event: BattleEvent, index: number) => void
        private actorLabels = new Map<string, any>()

        constructor() { super({ key: "expedition-battle" }) }
        configure(nextEvents: BattleEvent[], nextSpeed: number, nextReducedMotion: boolean, callback: (event: BattleEvent, index: number) => void) { this.replayEvents = nextEvents; this.replaySpeed = nextSpeed; this.compactMotion = nextReducedMotion; this.onReplayEvent = callback }
        create() {
          this.cameras.main.setBackgroundColor("#0b1719")
          this.add.rectangle(280, 142, 530, 226, 0x102524).setStrokeStyle(1, 0x31574f)
          this.add.text(20, 18, "BACKEND EVENT THEATER", { color: "#8ed9bd", fontFamily: "monospace", fontSize: "11px" })
          this.add.text(20, 38, "后端事件流 · Phaser 回放", { color: "#d8e8e2", fontFamily: "sans-serif", fontSize: "18px" })
          const actors = ["slot_1", "slot_2", "slot_3", "slot_4", "slot_5", "enemy_1", "enemy_2", "enemy_3"]
          actors.forEach((actor, index) => {
            const ally = index < 5
            const x = ally ? 76 + (index % 3) * 86 : 420 + ((index - 5) % 3) * 86
            const y = ally ? 132 + Math.floor(index / 3) * 54 : 132 + Math.floor((index - 5) / 3) * 54
            const box = this.add.rectangle(x, y, 70, 36, ally ? 0x20463d : 0x462d36).setStrokeStyle(1, ally ? 0x76c8a5 : 0xb36b79)
            const label = this.add.text(x, y, actor.replace("slot_", "位 "), { color: "#e0eee8", fontSize: "11px", align: "center" }).setOrigin(.5)
            this.actorLabels.set(actor, label); box.setData("actor", actor)
          })
          this.playEvents()
        }
        private playEvents() {
          if (!this.replayEvents.length) return
          let index = 0
          const next = () => {
            const event = this.replayEvents[index]
            if (!event) return
            this.onReplayEvent?.(event, index)
            const label = this.actorLabels.get(event.actor_id)
            if (label) {
              label.setColor(event.is_miss ? "#e8b27d" : event.is_critical ? "#ffcf88" : "#dcebe5")
              this.tweens.add({ targets: label, scale: event.is_critical ? 1.3 : 1.12, duration: this.compactMotion ? 0 : 120, yoyo: true })
            }
            index += 1
            if (index < this.replayEvents.length) this.time.delayedCall(this.compactMotion ? 20 : Math.max(80, 260 / this.replaySpeed), next)
          }
          next()
        }
      }
      const scene = new ExpeditionBattleScene()
      scene.configure(events, speed, reducedMotion, (_event, index) => setCursor(index))
      game = new Phaser.Game({ type: Phaser.AUTO, width: 560, height: 284, parent: mountRef.current, transparent: true, render: { antialias: true, roundPixels: true }, scene })
    }).catch(() => setCursor(-1))
    return () => { disposed = true; game?.destroy(true) }
  }, [events, speed, reducedMotion])

  const latest = cursor >= 0 ? events[cursor] : undefined
  return <section className="expedition-replay" aria-label="战斗回放">
    <div className="expedition-replay-head"><div><span className="expedition-kicker">BATTLE REPLAY · STAGE {stageNumber || "—"}</span><h3>{winner === "ally" ? "胜利回放" : winner ? "战斗记录" : "等待事件"}</h3></div><div className="expedition-speed" role="group" aria-label="回放速度">{[1, 2, 4].map(value => <button key={value} className={speed === value ? "active" : ""} onClick={() => setSpeed(value as 1 | 2 | 4)}>{value}x</button>)}</div></div>
    <div className="expedition-replay-stage" ref={mountRef} />
    <div className="expedition-replay-controls"><label><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)} /> reduced motion</label><span>{cursor + 1 > 0 ? `${cursor + 1} / ${events.length}` : `${events.length} 个事件`}</span><b>{latest ? `${eventLabel[latest.event_type] || latest.event_type} · ${latest.actor_id}` : "事件将按后端顺序播放"}</b></div>
    <div className="expedition-event-strip">{events.slice(Math.max(0, cursor - 2), cursor + 4).map(event => <span key={event.event_index} className={event.event_index === latest?.event_index ? "current" : ""}>{eventLabel[event.event_type] || event.event_type}</span>)}</div>
  </section>
}
