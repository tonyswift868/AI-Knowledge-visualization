import { useEffect, useRef, useState } from "react"
import type { AssetKind, BattleEvent, BattleUnit, Hero } from "./api"
import { resolveExpeditionAsset } from "./assetResolver"

type Props = { events: BattleEvent[]; winner?: string; stageNumber?: number; units?: BattleUnit[]; heroes?: Hero[] }
type ReplayScene = any

const eventLabel: Record<string, string> = {
  BATTLE_START: "战斗开始", INITIATIVE_ROLL: "行动顺序", ROUND_START: "回合开始", TURN_START: "轮到行动",
  NORMAL_ATTACK: "普通攻击", SKILL_CAST: "主动技能", DAMAGE: "造成伤害", CRITICAL: "暴击", MISS: "未命中",
  DODGE: "闪避", HEAL: "治疗", SHIELD_GAIN: "获得护盾", SHIELD_ABSORB: "护盾吸收", SHIELD_BREAK: "护盾破碎",
  STATUS_APPLY: "施加状态", STATUS_TICK: "状态结算", STATUS_REMOVE: "状态解除", CLEANSE: "净化", DISPEL: "驱散",
  ENERGY_CHANGE: "能量变化", COUNTER: "反击", TAUNT: "嘲讽", UNIT_DEATH: "单位退场", ROUND_END: "回合结束", BATTLE_END: "战斗结束",
}

function fallbackUrl(heroId: string, kind: AssetKind) {
  return "/assets/expedition/heroes/" + heroId + "/" + kind + ".svg"
}

function assetKeys(hero: Hero, kind: AssetKind) {
  const asset = resolveExpeditionAsset(hero, kind)
  return {
    asset,
    key: "expedition:" + asset.version + ":" + hero.hero_id + ":" + kind,
    fallbackKey: "expedition:fallback:" + hero.hero_id + ":" + kind,
  }
}

export function BattleReplay({ events, winner, stageNumber, units = [], heroes = [] }: Props) {
  const mountRef = useRef<HTMLDivElement>(null)
  const sceneRef = useRef<ReplayScene>(null)
  const [speed, setSpeed] = useState<1 | 2 | 4>(1)
  const [paused, setPaused] = useState(false)
  const [reducedMotion, setReducedMotion] = useState(false)
  const [cursor, setCursor] = useState(-1)
  const [loadProgress, setLoadProgress] = useState(0)
  const [loadWarning, setLoadWarning] = useState("")

  useEffect(() => {
    if (!mountRef.current) return
    let disposed = false
    let game: any
    setCursor(-1)
    setLoadProgress(0)
    setLoadWarning("")
    void import("phaser").then((module: any) => {
      if (disposed || !mountRef.current) return
      const Phaser = module.default || module
      class ExpeditionBattleScene extends Phaser.Scene {
        private replayEvents: BattleEvent[] = []
        private replayUnits: BattleUnit[] = []
        private replayHeroes: Hero[] = []
        private replaySpeed = 1
        private compactMotion = false
        private replayPaused = false
        private cursor = 0
        private nextTimer: any
        private onReplayEvent?: (event: BattleEvent, index: number) => void
        private onWarning?: (message: string) => void
        private actorViews = new Map<string, any>()
        private statusState = new Map<string, Set<string>>()
        private cutin: any

        constructor() { super({ key: "expedition-battle" }) }

        configure(nextEvents: BattleEvent[], nextUnits: BattleUnit[], nextHeroes: Hero[], nextSpeed: number, nextReducedMotion: boolean, onEvent: (event: BattleEvent, index: number) => void, onWarning: (message: string) => void) {
          this.replayEvents = nextEvents
          this.replayUnits = nextUnits
          this.replayHeroes = nextHeroes
          this.replaySpeed = nextSpeed
          this.compactMotion = nextReducedMotion
          this.onReplayEvent = onEvent
          this.onWarning = onWarning
        }

        preload() {
          const imageCount = this.replayHeroes.length * 3
          let loaded = 0
          this.load.on("progress", (value: number) => setLoadProgress(Math.round(value * 100)))
          this.load.on("loaderror", (file: any) => this.onWarning?.("资源加载失败，已回退：" + (file?.key || "unknown")))
          this.load.on("filecomplete", () => {
            loaded += 1
            if (imageCount) setLoadProgress(Math.min(100, Math.round((loaded / imageCount) * 100)))
          })
          for (const hero of this.replayHeroes) {
            for (const kind of ["avatar", "skill_cutin", "skill_icon"] as AssetKind[]) {
              const keys = assetKeys(hero, kind)
              this.load.image(keys.key, keys.asset.url)
              this.load.image(keys.fallbackKey, fallbackUrl(hero.hero_id, kind))
            }
          }
        }

        create() {
          this.cameras.main.setBackgroundColor("#0b1719")
          this.add.rectangle(340, 178, 650, 320, 0x102524).setStrokeStyle(1, 0x31574f)
          this.add.text(22, 16, "BACKEND EVENT THEATER", { color: "#8ed9bd", fontFamily: "monospace", fontSize: "11px" })
          this.add.text(22, 36, "后端事件流 · Phaser 战斗回放", { color: "#d8e8e2", fontFamily: "sans-serif", fontSize: "18px" })
          this.add.text(22, 61, "演出只读取已落盘事件，不重新计算伤害。", { color: "#7fa99b", fontFamily: "sans-serif", fontSize: "10px" })
          this.createActors()
          this.createCutin()
          this.playEvents()
        }

        private createActors() {
          const fallbackUnits = ["slot_1", "slot_2", "slot_3", "slot_4", "slot_5", "enemy_1", "enemy_2", "enemy_3"].map((id, index) => ({ id, hero_id: "", name: id, side: index < 5 ? "ally" : "enemy", position: index + 1, hp: 100, max_hp: 100, shield: 0, energy: 0, statuses: {}, alive: true } as BattleUnit))
          const source = this.replayUnits.length ? this.replayUnits : fallbackUnits
          for (const unit of source) {
            const ally = unit.side === "ally"
            const x = ally ? 88 + ((unit.position - 1) % 3) * 88 : 420 + ((unit.position - 1) % 3) * 88
            const y = ally ? 150 + Math.floor((unit.position - 1) / 3) * 82 : 150 + Math.floor((unit.position - 1) / 3) * 82
            const baseColor = ally ? 0x20463d : 0x462d36
            const body = this.add.rectangle(x, y, 76, 52, baseColor).setStrokeStyle(1, ally ? 0x76c8a5 : 0xb36b79)
            body.setData("baseColor", baseColor)
            const hero = this.replayHeroes.find(item => item.hero_id === unit.hero_id)
            const image = hero ? this.add.image(x, y - 3, assetKeys(hero, "avatar").key).setDisplaySize(34, 34) : null
            if (image && hero && !this.textures.exists(assetKeys(hero, "avatar").key)) image.setTexture(assetKeys(hero, "avatar").fallbackKey)
            const label = this.add.text(x, y + 17, unit.name.slice(0, 10), { color: "#e0eee8", fontSize: "9px", align: "center" }).setOrigin(.5)
            this.add.rectangle(x, y + 31, 62, 4, 0x081313).setOrigin(.5)
            const hpBar = this.add.rectangle(x - 31, y + 31, 62, 4, ally ? 0x78d3ac : 0xd87983).setOrigin(0, .5)
            const shieldBar = this.add.rectangle(x - 31, y + 37, 62, 3, 0x78a9e9).setOrigin(0, .5).setScale(0, 1)
            this.actorViews.set(unit.id, { body, image, label, hpBar, shieldBar, hp: unit.max_hp, maxHp: unit.max_hp, shield: 0, baseY: y, alive: true })
            this.statusState.set(unit.id, new Set())
          }
        }

        private createCutin() {
          const panel = this.add.rectangle(340, 100, 600, 62, 0x182f2d, .98).setStrokeStyle(1, 0x8ed9bd).setVisible(false)
          const text = this.add.text(340, 100, "", { color: "#e7f6ef", fontSize: "15px", align: "center", wordWrap: { width: 540 } }).setOrigin(.5).setVisible(false)
          this.cutin = { panel, text }
        }

        setSpeed(value: number) { this.replaySpeed = value }
        setReducedMotion(value: boolean) { this.compactMotion = value }

        setReplayPaused(value: boolean) {
          this.replayPaused = value
          if (!value) this.playEvents()
          else this.nextTimer?.remove(false)
        }

        restartReplay() {
          this.nextTimer?.remove(false)
          this.cursor = 0
          this.replayPaused = false
          for (const view of this.actorViews.values()) {
            view.hp = view.maxHp
            view.shield = 0
            view.alive = true
            view.body.setAlpha(1).clearTint().setFillStyle(view.body.getData("baseColor"), 1)
            view.image?.setAlpha(1).clearTint()
            view.label.setAlpha(1)
            view.hpBar.setScale(1, 1)
            view.shieldBar.setScale(0, 1)
            view.body.y = view.baseY
          }
          this.cutin.panel.setVisible(false)
          this.cutin.text.setVisible(false)
          this.playEvents()
        }

        skipReplay() {
          this.nextTimer?.remove(false)
          for (let index = this.cursor; index < this.replayEvents.length; index += 1) {
            const event = this.replayEvents[index]
            this.applyEvent(event)
            this.onReplayEvent?.(event, index)
          }
          this.cursor = Math.max(0, this.replayEvents.length - 1)
        }

        private playEvents() {
          if (this.replayPaused || this.cursor >= this.replayEvents.length) return
          const event = this.replayEvents[this.cursor]
          if (!event) return
          this.applyEvent(event)
          this.onReplayEvent?.(event, this.cursor)
          this.cursor += 1
          if (this.cursor < this.replayEvents.length) this.nextTimer = this.time.delayedCall(this.eventDuration(event), () => this.playEvents())
        }

        private eventDuration(event: BattleEvent) {
          if (this.compactMotion) return 100
          const base = event.event_type === "SKILL_CAST" ? 480 : event.event_type === "CRITICAL" ? 260 : 220
          return Math.max(45, Math.round(base / this.replaySpeed))
        }

        private applyEvent(event: BattleEvent) {
          const targets = event.target_ids || []
          if (event.event_type === "SKILL_CAST") this.showCutin(event)
          if (event.event_type === "DAMAGE") {
            for (const target of targets) this.updateHp(target, Number(event.numeric_values?.remaining_hp))
            this.float(targets, "-" + Number(event.numeric_values?.amount || 0), "#f0787e")
            this.flash(targets, 0xf0787e)
          } else if (event.event_type === "CRITICAL") {
            this.float(targets, "暴击", "#ffd279")
            this.cameras.main.shake(this.compactMotion ? 0 : 70, .002)
          } else if (event.event_type === "MISS") {
            this.float(targets, "未命中", "#b9c9c1")
            this.offset(targets, 7)
          } else if (event.event_type === "DODGE") {
            this.float(targets, "闪避", "#9fd3ee")
            this.offset(targets, 12)
          } else if (event.event_type === "HEAL") {
            for (const target of targets) this.updateHp(target, Number(event.numeric_values?.remaining_hp))
            this.float(targets, "+" + Number(event.numeric_values?.amount || 0), "#8ee4aa")
          } else if (event.event_type === "SHIELD_GAIN") {
            for (const target of targets) this.updateShield(target, Number(event.numeric_values?.amount || 0))
            this.float(targets, "护盾 +" + Number(event.numeric_values?.amount || 0), "#8ec9ff")
          } else if (event.event_type === "SHIELD_ABSORB") {
            for (const target of targets) this.updateShield(target, -Number(event.numeric_values?.amount || 0))
            this.float(targets, "吸收 " + Number(event.numeric_values?.amount || 0), "#8ec9ff")
          } else if (event.event_type === "SHIELD_BREAK") {
            for (const target of targets) this.updateShield(target, -999999)
            this.float(targets, "护盾破碎", "#8ec9ff")
          } else if (event.event_type === "STATUS_APPLY" || event.event_type === "STATUS_TICK" || event.event_type === "STATUS_REMOVE" || event.event_type === "CLEANSE") {
            for (const target of targets) this.updateStatus(target, event)
          } else if (event.event_type === "UNIT_DEATH") {
            for (const target of targets) this.death(target)
          }
        }

        private updateHp(id: string, next: number) {
          const view = this.actorViews.get(id)
          if (!view || !Number.isFinite(next)) return
          view.hp = Math.max(0, Math.min(view.maxHp, next))
          this.tweens.add({ targets: view.hpBar, scaleX: view.hp / Math.max(1, view.maxHp), duration: this.compactMotion ? 0 : 180 })
        }

        private updateShield(id: string, delta: number) {
          const view = this.actorViews.get(id)
          if (!view) return
          view.shield = Math.max(0, Math.min(view.maxHp, view.shield + delta))
          view.shieldBar.setScale(view.shield / Math.max(1, view.maxHp), 1)
        }

        private updateStatus(id: string, event: BattleEvent) {
          const state = this.statusState.get(id)
          if (!state) return
          const statuses = event.status_ids || []
          for (const status of statuses) {
            if (event.event_type === "STATUS_REMOVE" || event.event_type === "CLEANSE") state.delete(status)
            else state.add(status)
          }
          this.float([id], statuses.length ? statuses.join(" · ") : "状态", event.event_type === "STATUS_TICK" ? "#e0b56e" : "#caa8ff")
        }

        private float(ids: string[], message: string, color: string) {
          for (const id of ids) {
            const view = this.actorViews.get(id)
            if (!view) continue
            const text = this.add.text(view.body.x, view.baseY - 31, message, { color, fontSize: "12px", fontStyle: "bold" }).setOrigin(.5)
            this.tweens.add({ targets: text, y: view.baseY - 62, alpha: 0, duration: this.compactMotion ? 100 : 420, onComplete: () => text.destroy() })
          }
        }

        private flash(ids: string[], color: number) {
          for (const id of ids) {
            const view = this.actorViews.get(id)
            if (!view) continue
            view.body.setFillStyle(color, .72)
            this.time.delayedCall(this.compactMotion ? 60 : 130, () => view.body.setFillStyle(view.body.getData("baseColor"), 1))
          }
        }

        private offset(ids: string[], amount: number) {
          for (const id of ids) {
            const view = this.actorViews.get(id)
            if (!view) continue
            this.tweens.add({ targets: [view.body, view.image, view.label, view.hpBar, view.shieldBar], x: "+=" + amount, duration: this.compactMotion ? 0 : 90, yoyo: true })
          }
        }

        private death(id: string) {
          const view = this.actorViews.get(id)
          if (!view) return
          view.alive = false
          view.body.setTint(0x777777)
          view.image?.setTint(0x777777)
          this.tweens.add({ targets: [view.body, view.image, view.label], y: view.baseY + 14, alpha: .2, duration: this.compactMotion ? 100 : 320 })
        }

        private showCutin(event: BattleEvent) {
          const unit = this.replayUnits.find(item => item.id === event.actor_id)
          const hero = this.replayHeroes.find(item => item.hero_id === (event.actor_hero_id || unit?.hero_id))
          const skill = hero ? [hero.active_skill, hero.passive_one, hero.passive_two, hero.awakening].find(item => item?.skill_id === event.source_skill_id) : undefined
          if (hero) {
            const cutinKeys = assetKeys(hero, "skill_cutin")
            const iconKeys = assetKeys(hero, "skill_icon")
            this.cutin.image = this.cutin.image || this.add.image(90, 100, cutinKeys.key).setDisplaySize(210, 52).setDepth(4)
            this.cutin.icon = this.cutin.icon || this.add.image(48, 100, iconKeys.key).setDisplaySize(30, 30).setDepth(5)
            if (!this.textures.exists(cutinKeys.key)) this.cutin.image.setTexture(cutinKeys.fallbackKey)
            if (!this.textures.exists(iconKeys.key)) this.cutin.icon.setTexture(iconKeys.fallbackKey)
            this.cutin.image.setVisible(true)
            this.cutin.icon.setVisible(true)
          }
          this.cutin.text.setText((hero?.name || event.actor_id) + "  ·  " + (skill?.name || event.source_skill_id || "技能发动"))
          this.cutin.panel.setVisible(true)
          this.cutin.text.setVisible(true)
          this.cutin.panel.x = this.compactMotion ? 340 : 650
          this.cutin.text.x = this.cutin.panel.x
          const cutinTargets = [this.cutin.panel, this.cutin.text, this.cutin.image, this.cutin.icon].filter(Boolean)
          this.tweens.add({ targets: cutinTargets, x: 340, duration: this.compactMotion ? 100 : 260, ease: "Cubic.easeOut" })
          this.time.delayedCall(this.eventDuration(event), () => this.tweens.add({ targets: cutinTargets, alpha: 0, duration: this.compactMotion ? 0 : 150, onComplete: () => {
            this.cutin.panel.setVisible(false).setAlpha(1)
            this.cutin.text.setVisible(false).setAlpha(1)
            this.cutin.image?.setVisible(false).setAlpha(1)
            this.cutin.icon?.setVisible(false).setAlpha(1)
          } }))
        }
      }
      const scene = new ExpeditionBattleScene()
      scene.configure(events, units, heroes, speed, reducedMotion, (event: BattleEvent, index: number) => setCursor(index), warning => setLoadWarning(warning))
      sceneRef.current = scene
      game = new Phaser.Game({ type: Phaser.AUTO, width: 680, height: 360, parent: mountRef.current, transparent: true, render: { antialias: true, roundPixels: true }, scene })
    }).catch(() => setLoadWarning("Phaser 资源加载失败，当前仅显示事件条目"))
    return () => { disposed = true; sceneRef.current = null; game?.destroy(true) }
  }, [events, units, heroes])

  useEffect(() => { sceneRef.current?.setSpeed(speed) }, [speed])
  useEffect(() => { sceneRef.current?.setReducedMotion(reducedMotion) }, [reducedMotion])
  useEffect(() => { sceneRef.current?.setReplayPaused(paused) }, [paused])

  const latest = cursor >= 0 ? events[cursor] : undefined
  return <section className="expedition-replay" aria-label="战斗回放">
    <div className="expedition-replay-head"><div><span className="expedition-kicker">BATTLE REPLAY · STAGE {stageNumber || "—"}</span><h3>{winner === "ally" ? "胜利回放" : winner ? "战斗记录" : "等待事件"}</h3></div><div className="expedition-speed" role="group" aria-label="回放速度">{[1, 2, 4].map(value => <button key={value} className={speed === value ? "active" : ""} onClick={() => setSpeed(value as 1 | 2 | 4)}>{value}x</button>)}</div></div>
    <div className="expedition-replay-loading"><span>资源预加载 {loadProgress}%</span>{loadWarning && <b role="status">{loadWarning}</b>}</div>
    <div className="expedition-replay-stage" ref={mountRef} />
    <div className="expedition-replay-controls"><button className="expedition-small-button" onClick={() => setPaused(value => !value)}>{paused ? "继续" : "暂停"}</button><button className="expedition-small-button" onClick={() => sceneRef.current?.restartReplay()}>重播</button><button className="expedition-small-button" onClick={() => sceneRef.current?.skipReplay()}>跳过</button><label><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)} /> 减少动态</label><span>{cursor + 1 > 0 ? String(cursor + 1) + " / " + events.length : String(events.length) + " 个事件"}</span><b>{latest ? (eventLabel[latest.event_type] || latest.event_type) + " · " + (latest.actor_hero_id || latest.actor_id) : "事件将按后端顺序播放"}</b></div>
    <div className="expedition-event-strip">{events.slice(Math.max(0, cursor - 2), cursor + 4).map(event => <span key={event.event_index} className={event.event_index === latest?.event_index ? "current" : ""}>{eventLabel[event.event_type] || event.event_type}</span>)}</div>
  </section>
}
