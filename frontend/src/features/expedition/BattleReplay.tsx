import { useEffect, useRef, useState } from "react"
import type { AssetKind, BattleEvent, BattleUnit, Hero } from "./api"
import { fallbackAssetUrl, resolveExpeditionAsset } from "./assetResolver"
import { enemyBattlePortraitUrl } from "./enemyAssets"

type Props = { events: BattleEvent[]; winner?: string; stageNumber?: number; units?: BattleUnit[]; heroes?: Hero[] }
type ReplayScene = any

// The replay is intentionally taller than the compact first version.  A
// three-row formation needs enough vertical clearance for a portrait, its
// name, and its status bars; otherwise a tall character from row 1 visually
// occupies row 2 even though the logical cell is correct.
const BATTLE_WIDTH = 1080
const BATTLE_HEIGHT = 820
const BATTLE_BG_KEY = "expedition:battle:academy-duel-v1"
const BATTLE_BG_URL = "/assets/expedition/battle/academy-duel-v1.png"
// The team silhouettes are intentionally wide and close to the neutral lane.
// The grid is logical; the player should read faces and confrontation, not cells.
const FORMATION_X = [82, 252, 422]
const FORMATION_Y = [310, 520, 730]
const FORMATION_ROW_GAP = 210
const CN_DISPLAY_FONT = "'STXingkai', '华文行楷', 'KaiTi', '楷体', 'Microsoft YaHei', serif"
const FEMALE_ADULT_HEROES = new Set(["jinxiao", "shuangli"])
const DEFEATED_POSE_IDS = new Set([
  "jinxiao", "leishan", "gulu", "shuangli", "zhenchui",
  "row_crossbow", "poison_needle", "field_medic", "counter_guard", "bomb_golem",
])
const ACTION_POSE_IDS = new Set(DEFEATED_POSE_IDS)
const DEFEATED_FLIP_IDS = new Set(["counter_guard"])

function defeatedPoseVersion(heroId: string) { return heroId === "gulu" ? "kneel-v2" : "kneel-v1" }
function defeatedPoseKey(heroId: string) { return `expedition:defeated:${heroId}:${defeatedPoseVersion(heroId)}` }
function defeatedPoseUrl(heroId: string) { return `/assets/expedition/defeated/${heroId}/${defeatedPoseVersion(heroId)}.png` }
function actionPoseVersion(heroId: string) { return heroId === "gulu" ? "attack-v2" : "attack-v1" }
function actionPoseKey(heroId: string) { return `expedition:action:${heroId}:${actionPoseVersion(heroId)}` }
function actionPoseUrl(heroId: string) { return `/assets/expedition/actions/${heroId}/${actionPoseVersion(heroId)}.png` }

type AttackStyle = "blade" | "shield" | "hammer" | "crossbow" | "needle" | "spore" | "ice" | "staff" | "arcane"

const ATTACK_STYLE_BY_HERO: Record<string, AttackStyle> = {
  jinxiao: "blade",
  leishan: "shield",
  gulu: "spore",
  shuangli: "ice",
  zhenchui: "hammer",
  row_crossbow: "crossbow",
  poison_needle: "needle",
  field_medic: "staff",
  counter_guard: "shield",
  bomb_golem: "hammer",
}

const enemySkillPresentation: Record<string, { name: string; summary: string }> = {
  row_crossbow_active: { name: "破阵·横弩齐射", summary: "贯穿同一横排的连弩攻势" },
  poison_needle_active: { name: "蚀骨·三叠毒针", summary: "连续追击并叠加中毒" },
  field_medic_active: { name: "军阵·急救号令", summary: "救治生命最低的两名队友" },
  counter_guard_active: { name: "铁壁·反守为攻", summary: "架起减伤姿态并伺机反击" },
  bomb_golem_active: { name: "震爆·重槌落地", summary: "蓄力重击前排目标" },
}

const statusLabel: Record<string, string> = {
  burn: "灼烧", poison: "中毒", chill: "寒意", freeze: "冰冻", bleed: "流血",
  stun: "眩晕", silence: "沉默", taunt: "嘲讽", anti_heal: "禁疗", mirror_guard: "镜护",
  control_guard: "御控", damage_share: "分摊",
  damage_reduction: "减伤", counter_ready: "反击架势",
}

const eventLabel: Record<string, string> = {
  BATTLE_START: "战斗开始", INITIATIVE_ROLL: "行动顺序", ROUND_START: "回合开始", TURN_START: "行动",
  NORMAL_ATTACK: "普通攻击", SKILL_CAST: "主动技能", DAMAGE: "造成伤害", CRITICAL: "暴击", MISS: "未命中",
  DODGE: "闪避", HEAL: "治疗", SHIELD_GAIN: "获得护盾", SHIELD_ABSORB: "护盾吸收", SHIELD_BREAK: "护盾破裂",
  STATUS_APPLY: "施加状态", STATUS_TICK: "状态结算", STATUS_REMOVE: "状态解除", CLEANSE: "净化", DISPEL: "驱散",
  ENERGY_CHANGE: "能量变化", COUNTER: "反击", TAUNT: "嘲讽", UNIT_DEATH: "退场", ROUND_END: "回合结束", BATTLE_END: "战斗结束",
}

function assetKeys(hero: Hero, kind: AssetKind) {
  const asset = resolveExpeditionAsset(hero, kind)
  return {
    asset,
    key: `expedition:${asset.version}:${hero.hero_id}:${kind}`,
    fallbackKey: `expedition:fallback:${hero.hero_id}:${kind}`,
  }
}

const playerPortraitsFacingLeft = new Set(["shuangli"])
const enemyPortraitsFacingRight = new Set(["row_crossbow", "field_medic", "counter_guard"])
function shouldFlipPortrait(side: "ally" | "enemy", heroId: string) {
  return side === "enemy" ? enemyPortraitsFacingRight.has(heroId) : playerPortraitsFacingLeft.has(heroId)
}

function resolveSkillPresentation(event: BattleEvent, heroes: Hero[], units: BattleUnit[]) {
  const unit = units.find(item => item.id === event.actor_id)
  const hero = heroes.find(item => item.hero_id === (event.actor_hero_id || unit?.hero_id))
  const skill = hero ? [hero.active_skill, hero.passive_one, hero.passive_two, hero.awakening].find(item => item?.skill_id === event.source_skill_id) : undefined
  const enemyPresentation = enemySkillPresentation[String(event.source_skill_id || "")]
  return {
    unit,
    hero,
    name: skill?.name || enemyPresentation?.name || "技能发动",
    summary: skill?.summary || enemyPresentation?.summary || "战意凝聚，向敌阵发起攻势",
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

    const sceneHeroes = units.length
      ? heroes.filter(hero => units.some(unit => unit.hero_id === hero.hero_id))
      : heroes.slice(0, 8)

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
        private playbackToken = 0
        private fastForward = false
        private onReplayEvent?: (event: BattleEvent, index: number) => void
        private onWarning?: (message: string) => void
        private actorViews = new Map<string, any>()
        private statusState = new Map<string, Set<string>>()
        private activeSkillActors = new Set<string>()
        private skillReleaseStarted = new Set<string>()
        private skillTargetIds = new Map<string, string[]>()
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
          const enemyIds = this.replayUnits.filter(unit => unit.side === "enemy").map(unit => unit.hero_id)
          const defeatedIds = [...new Set(this.replayUnits.map(unit => unit.hero_id).filter(id => DEFEATED_POSE_IDS.has(id)))]
          const actionIds = [...new Set(this.replayUnits.map(unit => unit.hero_id).filter(id => ACTION_POSE_IDS.has(id)))]
          const imageCount = this.replayHeroes.length * 10 + enemyIds.length + defeatedIds.length + actionIds.length + 1
          let loaded = 0
          this.load.on("progress", (value: number) => setLoadProgress(Math.round(value * 100)))
          this.load.on("loaderror", (file: any) => this.onWarning?.(`素材加载失败，已回退：${file?.key || "unknown"}`))
          this.load.on("filecomplete", () => {
            loaded += 1
            if (imageCount) setLoadProgress(Math.min(100, Math.round((loaded / imageCount) * 100)))
          })
          for (const hero of this.replayHeroes) {
            for (const kind of ["portrait", "battle_portrait", "avatar", "skill_cutin", "skill_icon"] as AssetKind[]) {
              const keys = assetKeys(hero, kind)
              this.load.image(keys.key, keys.asset.url)
              this.load.image(keys.fallbackKey, fallbackAssetUrl(hero.hero_id, kind))
            }
          }
          this.load.image(BATTLE_BG_KEY, BATTLE_BG_URL)
          for (const enemyId of enemyIds) {
            const url = enemyBattlePortraitUrl(enemyId)
            if (url) this.load.image(`expedition:enemy:${enemyId}:battle`, url)
          }
          for (const heroId of defeatedIds) this.load.image(defeatedPoseKey(heroId), defeatedPoseUrl(heroId))
          for (const heroId of actionIds) this.load.image(actionPoseKey(heroId), actionPoseUrl(heroId))
        }

        create() {
          this.createBattlefield()
          this.createActors()
          this.createActionOrder()
          this.createCutin()
          this.playEvents()
        }

        private createBattlefield() {
          this.cameras.main.setBackgroundColor("#0a1118")
          if (this.textures.exists(BATTLE_BG_KEY)) {
            this.add.image(BATTLE_WIDTH / 2, BATTLE_HEIGHT / 2, BATTLE_BG_KEY).setDisplaySize(BATTLE_WIDTH, BATTLE_HEIGHT).setDepth(-20)
          } else {
            this.add.rectangle(BATTLE_WIDTH / 2, BATTLE_HEIGHT / 2, BATTLE_WIDTH, BATTLE_HEIGHT, 0x17222b).setDepth(-20)
          }
          // Wash the illustration back just enough for portraits, bars and hit FX.
          this.add.rectangle(BATTLE_WIDTH / 2, BATTLE_HEIGHT / 2, BATTLE_WIDTH, BATTLE_HEIGHT, 0x07111b, .34).setDepth(-19)
          this.add.rectangle(BATTLE_WIDTH / 2, 55, BATTLE_WIDTH, 110, 0x071019, .82).setDepth(-10)
          this.add.rectangle(265, 442, 500, 648, 0x0a3142, .16).setDepth(-9)
          this.add.rectangle(815, 442, 500, 648, 0x4a2024, .14).setDepth(-9)
          this.add.ellipse(BATTLE_WIDTH / 2, 690, 820, 126, 0x06101a, .26).setStrokeStyle(2, 0xd8b46a, .2).setDepth(-8)
          this.add.text(28, 21, `第 ${stageNumber || "—"} 关`, { color: "#f4e8cc", fontFamily: CN_DISPLAY_FONT, fontSize: "19px", fontStyle: "bold" }).setDepth(4)
          this.add.text(28, 50, "云台演武 · 阵前交锋", { color: "#9eb3b8", fontFamily: "Microsoft YaHei, sans-serif", fontSize: "11px" }).setDepth(4)
          this.add.text(190, 27, "我方", { color: "#8ee2ef", fontFamily: CN_DISPLAY_FONT, fontSize: "17px", fontStyle: "bold" }).setDepth(4)
          this.add.text(BATTLE_WIDTH - 190, 27, "敌阵", { color: "#ffaaa0", fontFamily: CN_DISPLAY_FONT, fontSize: "17px", fontStyle: "bold" }).setOrigin(1, 0).setDepth(4)
          this.add.text(BATTLE_WIDTH / 2, 26, "对 阵", { color: "#f1cb79", fontFamily: CN_DISPLAY_FONT, fontSize: "21px", fontStyle: "bold", stroke: "#17100a", strokeThickness: 3 }).setOrigin(.5, 0).setDepth(4)
        }

        private createActionOrder() {
          const order: string[] = []
          for (const event of this.replayEvents) {
            if (event.event_type !== "TURN_START" || order.includes(event.actor_id)) continue
            order.push(event.actor_id)
            if (order.length >= 8) break
          }
          const ids = order.length ? order : this.replayUnits.map(unit => unit.id)
          this.add.rectangle(BATTLE_WIDTH / 2 + 38, 82, 842, 30, 0x08131d, .88).setStrokeStyle(1, 0xc8a866, .28).setDepth(20)
          this.add.text(28, 75, "先攻序列", { color: "#a9bdc1", fontFamily: CN_DISPLAY_FONT, fontSize: "12px" }).setDepth(21)
          ids.slice(0, 8).forEach((id, index) => {
            const unit = this.replayUnits.find(item => item.id === id)
            const hero = this.replayHeroes.find(item => item.hero_id === unit?.hero_id)
            const x = 172 + index * 100
            const color = unit?.side === "enemy" ? 0xff7089 : 0x6bd5ff
            this.add.circle(x, 82, 11, color, .14).setStrokeStyle(1, color).setDepth(21)
            if (hero) {
              const avatar = assetKeys(hero, "avatar")
              this.add.image(x, 82, avatar.key).setDisplaySize(19, 19).setDepth(22)
            } else {
              this.add.text(x, 82, unit?.name?.slice(0, 1) || "?", { color: "#e7f3ff", fontFamily: CN_DISPLAY_FONT, fontSize: "12px", fontStyle: "bold" }).setOrigin(.5).setDepth(22)
            }
            this.add.text(x + 16, 76, `${index + 1}`, { color: index === 0 ? "#ffd279" : "#8faec3", fontFamily: "monospace", fontSize: "9px" }).setDepth(22)
          })
        }

        private slotPosition(side: "ally" | "enemy", unit: Pick<BattleUnit, "position" | "cell_id" | "grid_row" | "grid_col">) {
          // Formation coordinates are from each side's own perspective:
          // cell_0 is row 1 / column 1, the front-right slot for the player.
          // The enemy is mirrored because it faces the player from the right.
          const index = unit.cell_id?.startsWith("cell_")
            ? Number(unit.cell_id.slice("cell_".length))
            : (Number.isFinite(unit.grid_row) && Number.isFinite(unit.grid_col)
              ? Number(unit.grid_row) * 3 + Number(unit.grid_col)
              : Math.max(0, unit.position - 1))
          const safeIndex = Math.max(0, Math.min(8, Number.isFinite(index) ? index : 0))
          const row = Math.floor(safeIndex / 3)
          const col = safeIndex % 3
          // col 0 is front/right for the ally side, therefore x decreases
          // as the semantic column increases. Enemy uses the mirrored x.
          // cell_0 is column 1: the player's front/right position.  The
          // enemy sees the same semantic cell from the opposite side, so its
          // x coordinate is mirrored only after the row/column is resolved.
          const point = { x: FORMATION_X[2 - col], y: FORMATION_Y[row] }
          return side === "ally" ? point : { x: BATTLE_WIDTH - point.x, y: point.y }
        }

        private createStickEnemy(x: number, y: number, enemyId: string) {
          const presets: Record<string, { line: number; accent: number; weapon: "bow" | "needle" | "staff" | "shield" | "hammer" }> = {
            row_crossbow: { line: 0x78d9ff, accent: 0xff7b9d, weapon: "bow" },
            poison_needle: { line: 0xd49bff, accent: 0x9d70ff, weapon: "needle" },
            field_medic: { line: 0x8ee4aa, accent: 0xffd279, weapon: "staff" },
            counter_guard: { line: 0xffb86b, accent: 0xffe4a6, weapon: "shield" },
            bomb_golem: { line: 0xff806e, accent: 0xffc15c, weapon: "hammer" },
          }
          const preset = presets[enemyId] || { line: 0xff8796, accent: 0xffd279, weapon: "needle" as const }
          const figure = this.add.container(x, y - 2).setDepth(3)
          const g = this.add.graphics()
          g.lineStyle(6, preset.line, .95)
          g.strokeCircle(0, -103, 19)
          g.lineBetween(0, -83, 0, -34)
          g.lineBetween(0, -72, -31, -51)
          g.lineBetween(0, -72, 31, -51)
          g.lineBetween(0, -34, -23, 2)
          g.lineBetween(0, -34, 23, 2)
          g.lineStyle(3, preset.accent, .95)
          g.strokeCircle(0, -103, 9)
          g.fillStyle(preset.accent, .9)
          g.fillCircle(0, -103, 4)
          if (preset.weapon === "bow") {
            g.lineBetween(38, -78, 38, -27); g.arc(35, -52, 25, -Math.PI / 2, Math.PI / 2, false); g.lineBetween(35, -52, 70, -52)
          } else if (preset.weapon === "needle") {
            g.lineBetween(35, -76, 68, -103); g.lineBetween(59, -108, 68, -103); g.lineBetween(59, -98, 68, -103)
          } else if (preset.weapon === "staff") {
            g.lineBetween(42, -92, 42, 5); g.lineBetween(30, -82, 54, -82); g.lineBetween(35, -88, 49, -76)
          } else if (preset.weapon === "shield") {
            g.strokeCircle(39, -56, 25); g.lineBetween(20, -56, 58, -56); g.lineBetween(39, -75, 39, -37); g.lineStyle(4, preset.accent, .95); g.lineBetween(65, -84, 65, -26)
          } else {
            g.lineBetween(40, -72, 72, -8); g.fillStyle(preset.accent, .85); g.fillRect(55, -16, 30, 18)
          }
          figure.add(g)
          const base = this.add.ellipse(0, 7, 86, 16, preset.line, .14).setStrokeStyle(1, preset.line, .65)
          figure.add(base)
          return figure
        }

        private createActors() {
          const fallbackUnits = ["slot_1", "slot_2", "slot_3", "slot_4", "slot_5", "enemy_1", "enemy_2", "enemy_3"].map((id, index) => ({ id, hero_id: "", name: id, side: index < 5 ? "ally" : "enemy", position: index + 1, hp: 100, max_hp: 100, shield: 0, energy: 0, statuses: {}, alive: true } as BattleUnit))
          const source = this.replayUnits.length ? this.replayUnits : fallbackUnits
          for (const [actorIndex, unit] of source.entries()) {
            const side = unit.side === "ally" ? "ally" : "enemy"
            const { x, y } = this.slotPosition(side, unit)
            const hero = this.replayHeroes.find(item => item.hero_id === unit.hero_id)
            const portrait = hero ? assetKeys(hero, "battle_portrait") : null
            const enemyKey = `expedition:enemy:${unit.hero_id}:battle`
            const hasEnemyPortrait = Boolean(enemyBattlePortraitUrl(unit.hero_id))
            const row = Math.max(0, Math.min(2, Number(unit.grid_row ?? Math.round((y - FORMATION_Y[0]) / FORMATION_ROW_GAP))))
            const depth = 4 + row * 7
            const color = side === "ally" ? 0x78dbe8 : 0xff8b82
            const shadow = this.add.ellipse(x, y + 4, 104, 20, 0x05090d, .68).setStrokeStyle(1, color, .35).setDepth(depth)
            const aura = this.add.ellipse(x, y + 1, 118, 28, color, .08).setStrokeStyle(2, color, .46).setDepth(depth + 1)
            const motionRoot = this.add.container(x, y).setDepth(depth + 3)
            const idleRoot = this.add.container(0, 0)
            motionRoot.add(idleRoot)
            const textureKey = side === "ally" && portrait
              ? (this.textures.exists(portrait.key) ? portrait.key : portrait.fallbackKey)
              : (side === "enemy" && hasEnemyPortrait ? enemyKey : "")
            const image = textureKey ? this.add.image(0, 0, textureKey).setOrigin(.5, 1) : null
            const defeatedKey = DEFEATED_POSE_IDS.has(unit.hero_id) ? defeatedPoseKey(unit.hero_id) : ""
            const defeatedImage = defeatedKey && this.textures.exists(defeatedKey)
              ? this.add.image(0, 0, defeatedKey).setOrigin(.5, 1).setVisible(false)
              : null
            const actionKey = ACTION_POSE_IDS.has(unit.hero_id) ? actionPoseKey(unit.hero_id) : ""
            const actionImage = actionKey && this.textures.exists(actionKey)
              ? this.add.image(0, 0, actionKey).setOrigin(.5, 1).setVisible(false)
              : null
            let figure: any = null
            let badge: any = null
            if (image) {
              const maxHeight = unit.hero_id === "gulu" ? 154 : unit.hero_id === "bomb_golem" ? 190 : 184
              const maxWidth = ["leishan", "counter_guard", "bomb_golem"].includes(unit.hero_id) ? 132 : 118
              const scale = Math.min(maxWidth / Math.max(1, image.width), maxHeight / Math.max(1, image.height))
              image.setScale(scale)
              image.setFlipX(shouldFlipPortrait(side, unit.hero_id))
              idleRoot.add(image)
            } else if (side === "enemy") {
              figure = this.createStickEnemy(0, 6, unit.hero_id)
              idleRoot.add(figure)
            } else {
              badge = this.add.text(0, -80, unit.name.slice(0, 1), { color: "#b9ecff", fontSize: "38px", fontStyle: "bold" }).setOrigin(.5)
              idleRoot.add(badge)
            }
            if (defeatedImage) {
              const maxHeight = unit.hero_id === "gulu" ? 148 : unit.hero_id === "bomb_golem" ? 178 : 166
              const maxWidth = ["leishan", "counter_guard", "bomb_golem"].includes(unit.hero_id) ? 168 : 150
              const scale = Math.min(maxWidth / Math.max(1, defeatedImage.width), maxHeight / Math.max(1, defeatedImage.height))
              defeatedImage.setScale(scale)
              defeatedImage.setFlipX(DEFEATED_FLIP_IDS.has(unit.hero_id))
              idleRoot.add(defeatedImage)
            }
            if (actionImage) {
              const maxHeight = unit.hero_id === "gulu" ? 158 : unit.hero_id === "bomb_golem" ? 190 : 186
              const maxWidth = ["jinxiao", "row_crossbow", "shuangli", "zhenchui", "bomb_golem"].includes(unit.hero_id) ? 224 : 196
              const scale = Math.min(maxWidth / Math.max(1, actionImage.width), maxHeight / Math.max(1, actionImage.height))
              actionImage.setScale(scale)
              idleRoot.add(actionImage)
            }
            const label = this.add.text(x, y + 9, unit.name.slice(0, 10), { color: "#fff8e8", fontFamily: CN_DISPLAY_FONT, fontSize: "14px", fontStyle: "bold", align: "center", stroke: "#07101d", strokeThickness: 4 }).setOrigin(.5).setDepth(depth + 5)
            const hpTrack = this.add.rectangle(x - 56, y + 28, 112, 8, 0x071016, .95).setOrigin(0, .5).setDepth(depth + 5)
            const hpBar = this.add.rectangle(x - 56, y + 28, 112, 8, side === "ally" ? 0x54d5c4 : 0xff6f6b).setOrigin(0, .5).setDepth(depth + 6)
            const shieldBar = this.add.rectangle(x - 56, y + 39, 112, 4, 0x9cd5ff).setOrigin(0, .5).setScale(0, 1).setDepth(depth + 6)
            const energyBar = this.add.rectangle(x - 56, y + 45, 112, 3, 0xf4c86a).setOrigin(0, .5).setScale(Math.max(0, Math.min(1, unit.energy / 100)), 1).setDepth(depth + 6)
            const initialStatuses = Object.keys(unit.statuses || {}).map(status => statusLabel[status] || status).slice(0, 2).join(" · ")
            const statusText = this.add.text(x, y + 50, initialStatuses, { color: "#ffe09a", fontFamily: "Microsoft YaHei, sans-serif", fontSize: "10px", fontStyle: "bold", stroke: "#07101d", strokeThickness: 3 }).setOrigin(.5, 0).setDepth(depth + 7)
            const view = { id: unit.id, shadow, aura, motionRoot, idleRoot, image, actionImage, defeatedImage, figure, badge, label, hpTrack, hpBar, shieldBar, energyBar, statusText, hp: unit.max_hp, maxHp: unit.max_hp, shield: 0, baseX: x, baseY: y, baseDepth: depth + 3, side, heroId: unit.hero_id, alive: true, teamColor: color, baseShadowAlpha: .68, poseToken: 0, baseImageScaleX: image?.scaleX ?? 1, baseImageScaleY: image?.scaleY ?? 1, baseActionScaleX: actionImage?.scaleX ?? 1, baseActionScaleY: actionImage?.scaleY ?? 1, baseDefeatedScaleX: defeatedImage?.scaleX ?? 1, baseDefeatedScaleY: defeatedImage?.scaleY ?? 1 }
            this.actorViews.set(unit.id, view)
            this.statusState.set(unit.id, new Set())
            this.startIdle(view, actorIndex)
          }
        }

        private startIdle(view: any, index: number) {
          const phase = (index % 5) * 130
          const bob = this.tweens.add({ targets: view.idleRoot, y: -3 - (index % 2), duration: 900 + (index % 4) * 110, delay: phase, yoyo: true, repeat: -1, ease: "Sine.InOut" })
          const sway = this.tweens.add({ targets: view.idleRoot, angle: view.side === "ally" ? .8 : -.8, duration: 1450 + (index % 3) * 170, delay: phase / 2, yoyo: true, repeat: -1, ease: "Sine.InOut" })
          view.idleTweens = [bob, sway]
          if (!view.image) {
            if (this.compactMotion) view.idleTweens.forEach((tween: any) => tween.pause())
            return
          }
          const isCreature = view.heroId === "gulu" || view.heroId === "bomb_golem"
          const isAdultWoman = FEMALE_ADULT_HEROES.has(view.heroId)
          const breatheY = isCreature ? 1.016 : isAdultWoman ? 1.008 : 1.004
          const breatheX = isCreature ? .988 : isAdultWoman ? .996 : .998
          const breath = this.tweens.add({
            targets: view.image,
            scaleY: view.baseImageScaleY * breatheY,
            scaleX: view.baseImageScaleX * breatheX,
            duration: isCreature ? 720 : isAdultWoman ? 840 : 1100,
            delay: 90 + phase,
            yoyo: true,
            repeat: -1,
            ease: "Sine.InOut",
          })
          view.idleTweens.push(breath)
          if (this.compactMotion) view.idleTweens.forEach((tween: any) => tween.pause())
        }

        private createCutin() {
          const center = BATTLE_WIDTH / 2
          const cutinY = BATTLE_HEIGHT / 2
          const blackout = this.add.rectangle(center, cutinY, BATTLE_WIDTH, BATTLE_HEIGHT, 0x020507, .7).setVisible(false).setDepth(40)
          const panel = this.add.rectangle(center, cutinY, 1040, 286, 0x101316, .96).setStrokeStyle(2, 0xe7c16e, .72).setVisible(false).setDepth(41)
          const wash = this.add.rectangle(center, cutinY, 1016, 260, 0x2a1915, .44).setVisible(false).setDepth(42)
          const topLine = this.add.rectangle(center, cutinY - 137, 980, 3, 0xe7c16e, .9).setVisible(false).setDepth(44)
          const bottomLine = this.add.rectangle(center, cutinY + 137, 980, 3, 0xe7c16e, .62).setVisible(false).setDepth(44)
          const image = this.add.image(275, cutinY, "").setVisible(false).setDepth(43)
          const caption = this.add.text(585, 284, "奥义发动", { color: "#e5c47b", fontFamily: CN_DISPLAY_FONT, fontSize: "18px", fontStyle: "bold" }).setVisible(false).setDepth(45)
          const text = this.add.text(585, 338, "", { color: "#fff7e7", fontFamily: CN_DISPLAY_FONT, fontSize: "46px", fontStyle: "bold", stroke: "#2b120e", strokeThickness: 7, align: "left", wordWrap: { width: 430 } }).setOrigin(0, .5).setVisible(false).setDepth(45)
          const summary = this.add.text(588, 401, "", { color: "#e8d8bd", fontFamily: "Microsoft YaHei, sans-serif", fontSize: "15px", fontStyle: "bold", wordWrap: { width: 410 } }).setVisible(false).setDepth(45)
          const actor = this.add.text(588, 457, "", { color: "#9fb5b8", fontFamily: CN_DISPLAY_FONT, fontSize: "15px" }).setVisible(false).setDepth(45)
          const seal = this.add.circle(1010, 470, 34, 0x6c251d, .6).setStrokeStyle(2, 0xe7c16e, .8).setVisible(false).setDepth(45)
          const sealText = this.add.text(1010, 470, "战", { color: "#f0d28b", fontFamily: CN_DISPLAY_FONT, fontSize: "24px", fontStyle: "bold" }).setOrigin(.5).setVisible(false).setDepth(46)
          const nodes = [blackout, panel, wash, topLine, bottomLine, image, caption, text, summary, actor, seal, sealText]
          this.cutin = { blackout, panel, wash, topLine, bottomLine, image, caption, text, summary, actor, seal, sealText, nodes }
        }

        setSpeed(value: number) { this.replaySpeed = Math.max(1, Math.min(4, Number(value) || 1)); this.scheduleNext() }
        setReducedMotion(value: boolean) {
          this.compactMotion = value
          for (const view of this.actorViews.values()) {
            view.idleTweens?.forEach((tween: any) => value || !view.alive ? tween.pause() : tween.resume())
            if (value) {
              view.idleRoot?.setPosition(0, 0).setAngle(0)
              view.image?.setScale(view.baseImageScaleX, view.baseImageScaleY)
              view.actionImage?.setScale(view.baseActionScaleX, view.baseActionScaleY)
              view.defeatedImage?.setScale(view.baseDefeatedScaleX, view.baseDefeatedScaleY)
            }
          }
        }

        setReplayPaused(value: boolean) {
          this.replayPaused = value
          this.playbackToken += 1
          this.nextTimer?.remove(false)
          this.nextTimer = undefined
          if (!value) this.scheduleNext()
        }

        restartReplay() {
          this.playbackToken += 1
          this.nextTimer?.remove(false)
          this.nextTimer = undefined
          this.cursor = 0
          this.activeSkillActors.clear()
          this.skillReleaseStarted.clear()
          this.skillTargetIds.clear()
          if (this.replayEvents[0]) this.onReplayEvent?.(this.replayEvents[0], -1)
          for (const view of this.actorViews.values()) {
            view.hp = view.maxHp; view.shield = 0; view.alive = true
            this.tweens.killTweensOf(view.motionRoot)
            this.tweens.killTweensOf(view.shadow)
            view.shadow.setPosition(view.baseX, view.baseY + 4).setScale(1).setAlpha(view.baseShadowAlpha)
            view.aura.setPosition(view.baseX, view.baseY + 1).setAlpha(1).setScale(1).setFillStyle(view.teamColor, .08)
            view.motionRoot?.setPosition(view.baseX, view.baseY).setScale(1).setAngle(0).setAlpha(1)
            view.motionRoot?.setDepth(view.baseDepth)
            view.idleRoot?.setPosition(0, 0).setScale(1).setAngle(0).setAlpha(1)
            view.image?.setVisible(true).setScale(view.baseImageScaleX, view.baseImageScaleY).setAlpha(1).clearTint()
            view.actionImage?.setVisible(false).setScale(view.baseActionScaleX, view.baseActionScaleY).setAlpha(1).clearTint()
            view.defeatedImage?.setVisible(false).setScale(view.baseDefeatedScaleX, view.baseDefeatedScaleY).setAlpha(1).clearTint()
            view.figure?.setVisible(true).setAlpha(1)
            view.badge?.setVisible(true).setAlpha(1).clearTint()
            view.label.setPosition(view.baseX, view.baseY + 9).setAlpha(1)
            view.hpTrack.setPosition(view.baseX - 56, view.baseY + 28).setAlpha(1)
            view.hpBar.setPosition(view.baseX - 56, view.baseY + 28).setScale(1, 1).setAlpha(1)
            view.shieldBar.setPosition(view.baseX - 56, view.baseY + 39).setScale(0, 1).setAlpha(1)
            view.energyBar.setPosition(view.baseX - 56, view.baseY + 45).setAlpha(1)
            view.statusText?.setPosition(view.baseX, view.baseY + 50).setText("").setAlpha(1)
            if (!this.compactMotion) view.idleTweens?.forEach((tween: any) => tween.restart())
          }
          for (const state of this.statusState.values()) state.clear()
          this.cutin?.nodes?.forEach((node: any) => node.setVisible(false).setAlpha(1))
          this.scheduleNext()
        }

        skipReplay() {
          this.playbackToken += 1; this.nextTimer?.remove(false); this.nextTimer = undefined; this.fastForward = true
          for (let index = this.cursor; index < this.replayEvents.length; index += 1) { const event = this.replayEvents[index]; this.applyEvent(event); this.onReplayEvent?.(event, index) }
          this.fastForward = false; this.cursor = this.replayEvents.length ? this.replayEvents.length - 1 : -1
        }

        private scheduleNext() {
          this.nextTimer?.remove(false); this.nextTimer = undefined
          if (this.replayPaused || this.cursor >= this.replayEvents.length) return
          const event = this.replayEvents[this.cursor]; if (!event) return
          const token = ++this.playbackToken
          this.nextTimer = this.time.delayedCall(this.eventDuration(event), () => { if (token === this.playbackToken) this.playEvents() })
        }

        private playEvents() {
          if (this.replayPaused || this.cursor >= this.replayEvents.length) return
          const event = this.replayEvents[this.cursor]; if (!event) return
          this.applyEvent(event); this.onReplayEvent?.(event, this.cursor); this.cursor += 1; this.scheduleNext()
        }

        private eventDuration(event: BattleEvent) {
          if (this.compactMotion) return event.event_type === "SKILL_CAST" ? 760 : 110
          const style = this.attackStyle(this.actorViews.get(event.actor_id))
          const meleeResult = event.event_type === "DAMAGE" && ["blade", "shield", "hammer"].includes(style)
          const skillResult = this.activeSkillActors.has(event.actor_id) && ["DAMAGE", "HEAL", "SHIELD_GAIN", "STATUS_APPLY"].includes(event.event_type)
          const base = event.event_type === "SKILL_CAST" ? 1850
            : meleeResult ? (skillResult ? 980 : 820)
              : event.event_type === "DAMAGE" ? (skillResult ? 720 : 540)
                : skillResult ? 620
                  : event.event_type === "CRITICAL" ? 460
                    : event.event_type === "NORMAL_ATTACK" ? 300 : 155
          const minimum = event.event_type === "SKILL_CAST" ? 620 : meleeResult ? 330 : event.event_type === "DAMAGE" ? 280 : skillResult ? 210 : 45
          return Math.max(minimum, Math.round(base / this.replaySpeed))
        }

        private upcomingSkillTargets(actorId: string) {
          const ids: string[] = []
          for (let index = this.cursor + 1; index < this.replayEvents.length; index += 1) {
            const next = this.replayEvents[index]
            if (next.event_type === "TURN_START" || next.event_type === "BATTLE_END") break
            if (next.actor_id !== actorId || !["DAMAGE", "HEAL", "SHIELD_GAIN", "STATUS_APPLY", "STATUS_TICK"].includes(next.event_type)) continue
            for (const id of next.target_ids || []) if (!ids.includes(id)) ids.push(id)
          }
          return ids
        }

        private applyEvent(event: BattleEvent) {
          const targets = event.target_ids || []
          const visual = !this.fastForward
          if (event.event_type === "TURN_START") {
            this.activeSkillActors.clear()
            this.skillReleaseStarted.clear()
            this.skillTargetIds.clear()
          }
          if (event.event_type === "SKILL_CAST") {
            this.activeSkillActors.add(event.actor_id)
            this.skillReleaseStarted.delete(event.actor_id)
            this.skillTargetIds.set(event.actor_id, this.upcomingSkillTargets(event.actor_id))
            if (visual) this.showCutin(event)
          }
          if (event.event_type === "NORMAL_ATTACK") {
            this.activeSkillActors.delete(event.actor_id)
            this.skillReleaseStarted.delete(event.actor_id)
            this.skillTargetIds.delete(event.actor_id)
            if (visual) this.prepareAction(event, false)
          }
          const skillResult = this.activeSkillActors.has(event.actor_id)
          const firstSkillResult = skillResult && !this.skillReleaseStarted.has(event.actor_id)
          if (firstSkillResult && ["DAMAGE", "HEAL", "SHIELD_GAIN", "STATUS_APPLY", "STATUS_TICK"].includes(event.event_type)) {
            this.skillReleaseStarted.add(event.actor_id)
            if (visual) this.animateSkillRelease(event, event.event_type !== "DAMAGE")
          }
          if (event.event_type === "DAMAGE") {
            for (const target of targets) this.updateHp(target, Number(event.numeric_values?.remaining_hp))
            if (visual) this.animateHit(event, Boolean(event.is_critical), skillResult, firstSkillResult)
          } else if (event.event_type === "CRITICAL") {
            if (visual) { this.float(targets, "暴击", "#ffd279"); this.cameras.main.shake(this.compactMotion ? 0 : 70, .002); this.burst(targets, 0xffd279) }
          } else if (event.event_type === "MISS") {
            if (visual) { this.float(targets, "未命中", "#b9c9c1"); this.offset(targets, 7) }
          } else if (event.event_type === "DODGE") {
            if (visual) { this.float(targets, "闪避", "#9fd3ee"); this.offset(targets, 12) }
          } else if (event.event_type === "HEAL") {
            for (const target of targets) this.updateHp(target, Number(event.numeric_values?.remaining_hp))
            if (visual) { this.float(targets, `+${Number(event.numeric_values?.amount || 0)}`, "#8ee4aa"); this.supportFx(targets, 0x8ee4aa) }
          } else if (event.event_type === "SHIELD_GAIN") {
            for (const target of targets) this.updateShield(target, Number(event.numeric_values?.amount || 0))
            if (visual) { this.float(targets, `护盾 +${Number(event.numeric_values?.amount || 0)}`, "#8ec9ff"); this.supportFx(targets, 0x8ec9ff) }
          } else if (event.event_type === "SHIELD_ABSORB") {
            for (const target of targets) this.updateShield(target, -Number(event.numeric_values?.amount || 0))
            if (visual) this.float(targets, `吸收 ${Number(event.numeric_values?.amount || 0)}`, "#8ec9ff")
          } else if (event.event_type === "SHIELD_BREAK") {
            for (const target of targets) this.updateShield(target, -999999)
            if (visual) this.float(targets, "护盾破裂", "#8ec9ff")
          } else if (event.event_type === "COUNTER") {
            if (visual) { this.float([event.actor_id], "反击！", "#ffd279"); this.burst([event.actor_id], 0xffb86b) }
          } else if (["STATUS_APPLY", "STATUS_TICK", "STATUS_REMOVE", "CLEANSE"].includes(event.event_type)) {
            for (const target of targets) this.updateStatus(target, event, visual)
          } else if (event.event_type === "UNIT_DEATH") {
            for (const target of targets) this.death(target, visual)
          }
        }

        private combatNodes(view: any) { return [view.motionRoot].filter(Boolean) }

        private hudNodes(view: any) { return [view.label, view.hpTrack, view.hpBar, view.shieldBar, view.energyBar, view.statusText].filter(Boolean) }

        private snapActionPose(view: any) {
          if (!view?.motionRoot) return
          view.motionRoot.setPosition(view.baseX, view.baseY).setScale(1, 1).setAngle(0)
        }

        private interruptActionPose(view: any) {
          if (!view?.alive) return
          this.tweens.killTweensOf(view.motionRoot)
          this.tweens.killTweensOf(view.shadow)
          this.snapActionPose(view)
          view.shadow.setPosition(view.baseX, view.baseY + 4).setScale(1, 1).setAlpha(view.baseShadowAlpha)
        }

        private showActionPose(view: any) {
          if (!view?.alive || !view.actionImage) return 0
          view.poseToken = Number(view.poseToken || 0) + 1
          view.image?.setVisible(false)
          view.figure?.setVisible(false)
          view.badge?.setVisible(false)
          view.actionImage.setVisible(true).setAlpha(1).clearTint()
          return view.poseToken
        }

        private showIdlePose(view: any, token?: number) {
          if (!view?.alive || (token && token !== view.poseToken)) return
          view.actionImage?.setVisible(false)
          view.image?.setVisible(true)
          view.figure?.setVisible(true)
          view.badge?.setVisible(true)
        }

        private attackStyle(view: any): AttackStyle {
          return ATTACK_STYLE_BY_HERO[view?.heroId] || "arcane"
        }

        private weaponAnchor(view: any, direction: number, style = this.attackStyle(view)) {
          const forward = style === "crossbow" ? 72 : style === "needle" ? 58 : style === "staff" || style === "ice" ? 68 : style === "spore" ? 60 : style === "hammer" ? 42 : style === "shield" ? 48 : 38
          const height = style === "hammer" ? 118 : style === "crossbow" ? 102 : style === "needle" ? 112 : style === "spore" ? 112 : style === "ice" || style === "staff" ? 122 : 94
          return { x: view.motionRoot.x + direction * forward, y: view.motionRoot.y - height }
        }

        private motionMs(base: number, minimum = 45) { return Math.max(minimum, Math.round(base / this.replaySpeed)) }

        private prepareAction(event: BattleEvent, skill: boolean) {
          const view = this.actorViews.get(event.actor_id); if (!view || !view.alive) return
          const target = this.actorViews.get((event.target_ids || [])[0])
          const direction = target ? Math.sign(target.baseX - view.baseX) || (view.side === "ally" ? 1 : -1) : (view.side === "ally" ? 1 : -1)
          const style = this.attackStyle(view)
          const anchor = this.weaponAnchor(view, direction, style)
          const color = skill ? 0xffd77d : style === "blade" || style === "hammer" ? 0xff9a63 : style === "spore" ? 0xb48cff : style === "ice" ? 0x8feaff : 0x92dcff
          const charge = this.add.circle(anchor.x, anchor.y, skill ? 24 : 12, color, .16).setStrokeStyle(skill ? 5 : 3, color, .92).setDepth(35)
          const spark = this.add.rectangle(anchor.x, anchor.y, skill ? 74 : 42, skill ? 6 : 3, color, .84).setRotation(direction > 0 ? -.18 : Math.PI + .18).setDepth(34)
          this.tweens.add({ targets: charge, scale: skill ? 2.1 : 1.55, alpha: 0, duration: this.compactMotion ? 90 : skill ? 700 : 260, ease: "Cubic.Out", onComplete: () => charge.destroy() })
          this.tweens.add({ targets: spark, scaleX: 1.5, alpha: 0, duration: this.compactMotion ? 80 : skill ? 430 : 210, onComplete: () => spark.destroy() })
          if (this.compactMotion) return
          this.interruptActionPose(view)
          const pullBack = -direction * (skill ? 12 : 7)
          const windupAngle = direction * (style === "hammer" ? -4.8 : style === "blade" ? -3.2 : style === "crossbow" || style === "needle" ? -1.4 : -2.2)
          this.tweens.add({
            targets: view.motionRoot,
            x: view.baseX + pullBack,
            y: view.baseY + (skill ? 6 : 3),
            angle: windupAngle,
            duration: skill ? 210 : 105,
            yoyo: true,
            ease: "Quad.InOut",
            onComplete: () => view.alive && this.snapActionPose(view),
          })
        }

        private animateSkillRelease(event: BattleEvent, showPose: boolean) {
          const actor = this.actorViews.get(event.actor_id); if (!actor || !actor.alive) return
          const style = this.attackStyle(actor)
          const direction = actor.side === "ally" ? 1 : -1
          const palette: Record<AttackStyle, number> = {
            blade: 0xff6b59, shield: 0xffc36d, hammer: 0xff7a5c, crossbow: 0x75dcff,
            needle: 0xc28cff, spore: 0xb68cff, ice: 0x8feaff, staff: 0x87edbd, arcane: 0xf0cf77,
          }
          const color = palette[style]
          const targetViews = (this.skillTargetIds.get(event.actor_id) || event.target_ids || []).map(id => this.actorViews.get(id)).filter(Boolean)
          for (const target of targetViews) {
            const marker = this.add.ellipse(target.baseX, target.baseY - 82, 78, 118, color, .06).setStrokeStyle(3, color, .88).setScale(.55).setDepth(33)
            this.tweens.add({ targets: marker, scale: 1.18, alpha: 0, duration: this.motionMs(480, 160), ease: "Cubic.Out", onComplete: () => marker.destroy() })
          }
          const rowTargets = targetViews.length > 1 && targetViews.every((target: any) => Math.abs(target.baseY - targetViews[0].baseY) < 4)
          if (rowTargets) {
            const left = Math.min(...targetViews.map((target: any) => target.baseX)) - 56
            const right = Math.max(...targetViews.map((target: any) => target.baseX)) + 56
            const sweep = this.add.rectangle((left + right) / 2, targetViews[0].baseY - 88, right - left, 9, color, .84).setScale(.06, 1).setDepth(36)
            const edge = this.add.rectangle((left + right) / 2, targetViews[0].baseY - 88, right - left, 2, 0xffffff, .92).setScale(.06, 1).setDepth(37)
            this.tweens.add({ targets: [sweep, edge], scaleX: 1, duration: this.motionMs(170, 65), ease: "Expo.Out" })
            this.tweens.add({ targets: [sweep, edge], alpha: 0, delay: this.motionMs(150, 55), duration: this.motionMs(240, 90), onComplete: () => { sweep.destroy(); edge.destroy() } })
          }
          const ring = this.add.ellipse(actor.baseX, actor.baseY - 72, style === "shield" ? 126 : 88, style === "shield" ? 176 : 112, color, .08)
            .setStrokeStyle(style === "shield" ? 6 : 3, color, .92).setScale(.28).setDepth(37)
          this.tweens.add({ targets: ring, scale: style === "shield" ? 1.55 : 1.9, alpha: 0, duration: this.motionMs(430, 150), ease: "Cubic.Out", onComplete: () => ring.destroy() })

          const marks: any[] = []
          if (style === "blade") {
            for (let i = -1; i <= 1; i += 1) marks.push(this.add.rectangle(actor.baseX + i * 18, actor.baseY - 96 + i * 12, 96, i === 0 ? 7 : 4, i === 0 ? 0xfff0c8 : color, .94).setRotation(direction * (-.72 + i * .22)).setScale(.12, 1).setDepth(38))
          } else if (style === "hammer") {
            marks.push(this.add.rectangle(actor.baseX, actor.baseY - 150, 18, 148, color, .88).setOrigin(.5, 0).setScale(1, .15).setDepth(38))
            marks.push(this.add.ellipse(actor.baseX, actor.baseY, 110, 26, color, .16).setStrokeStyle(5, color, .92).setScale(.25).setDepth(38))
            if (!this.compactMotion) this.cameras.main.shake(90, .0025)
          } else if (style === "crossbow" || style === "needle") {
            const count = style === "needle" ? 5 : 3
            for (let i = 0; i < count; i += 1) marks.push(this.add.rectangle(actor.baseX + direction * (42 + i * 7), actor.baseY - 122 + (i - (count - 1) / 2) * 12, style === "needle" ? 44 : 78, style === "needle" ? 3 : 5, color, .9).setRotation(direction > 0 ? -.08 : Math.PI + .08).setScale(.25, 1).setDepth(38))
          } else {
            const count = style === "spore" ? 7 : style === "ice" ? 6 : 4
            for (let i = 0; i < count; i += 1) {
              const angle = (Math.PI * 2 * i) / count
              const px = actor.baseX + Math.cos(angle) * (style === "ice" ? 52 : 40)
              const py = actor.baseY - 84 + Math.sin(angle) * (style === "ice" ? 64 : 46)
              const mark = style === "ice"
                ? this.add.rectangle(px, py, 12, 28, color, .9).setRotation(angle).setDepth(38)
                : this.add.circle(px, py, style === "spore" ? 9 : 7, color, .86).setStrokeStyle(2, 0xf4fff9, .72).setDepth(38)
              marks.push(mark)
            }
          }
          marks.forEach((mark, index) => this.tweens.add({ targets: mark, scaleX: 1.25, scaleY: 1.25, y: mark.y - 18 - index * 2, alpha: 0, delay: index * this.motionMs(24, 8), duration: this.motionMs(330, 120), ease: "Cubic.Out", onComplete: () => mark.destroy() }))
          if (showPose) {
            const poseToken = this.showActionPose(actor)
            if (poseToken) this.time.delayedCall(this.motionMs(520, 190), () => this.showIdlePose(actor, poseToken))
          }
          if (!this.compactMotion) {
            this.interruptActionPose(actor)
            this.tweens.add({ targets: actor.motionRoot, y: actor.baseY - 12, angle: direction * (style === "blade" ? -4 : style === "hammer" ? -6 : 2), duration: this.motionMs(150, 55), yoyo: true, ease: "Sine.Out", onComplete: () => actor.alive && this.snapActionPose(actor) })
          }
        }

        private emitWeaponFx(actor: any, target: any, critical: boolean, special: boolean, onImpact: () => void) {
          const style = this.attackStyle(actor)
          const direction = Math.sign(target.baseX - actor.baseX) || (actor.side === "ally" ? 1 : -1)
          const origin = this.weaponAnchor(actor, direction, style)
          const impact = { x: target.baseX - direction * 12, y: target.baseY - (style === "hammer" ? 54 : 92) }
          const color = critical ? 0xffd36b : style === "blade" || style === "hammer" ? 0xff7b58 : style === "spore" || style === "needle" ? 0xb987ff : style === "ice" ? 0x8be9ff : style === "staff" ? 0x8ff0b1 : 0x8ed9ff
          const duration = this.compactMotion ? 40 : this.motionMs(special ? 190 : 150, 70)

          if (style === "blade") {
            const slashA = this.add.rectangle(impact.x, impact.y, special ? 176 : 132, critical || special ? 11 : 7, color, .95).setRotation(direction * -.66).setScale(.12, 1).setDepth(36)
            const slashB = this.add.rectangle(impact.x + direction * 5, impact.y + 12, special ? 128 : 92, special ? 6 : 4, 0xffe6bd, .82).setRotation(direction * .42).setScale(.1, 1).setDepth(36)
            this.tweens.add({ targets: slashA, scaleX: 1.15, alpha: 0, duration: duration + 90, ease: "Cubic.Out", onComplete: () => slashA.destroy() })
            this.tweens.add({ targets: slashB, scaleX: 1, alpha: 0, delay: 28, duration: duration + 70, ease: "Cubic.Out", onComplete: () => slashB.destroy() })
            this.burst([target.id], color)
            onImpact()
            return
          }

          if (style === "hammer") {
            const drop = this.add.rectangle(impact.x - direction * 18, impact.y - 45, critical ? 18 : 13, 142, color, .88).setOrigin(.5, 1).setRotation(direction * .16).setScale(1, .15).setDepth(36)
            const ground = this.add.ellipse(target.baseX, target.baseY - 2, 54, 16, color, .28).setStrokeStyle(4, color, .92).setScale(.2).setDepth(36)
            this.tweens.add({ targets: drop, scaleY: 1, alpha: 0, duration: duration + 80, ease: "Expo.Out", onComplete: () => drop.destroy() })
            this.tweens.add({ targets: ground, scaleX: critical ? 2.5 : 1.9, scaleY: critical ? 2.1 : 1.6, alpha: 0, duration: duration + 150, ease: "Cubic.Out", onComplete: () => ground.destroy() })
            if (!this.compactMotion) this.cameras.main.shake(critical ? 105 : 65, critical ? .004 : .0022)
            this.burst([target.id], color)
            onImpact()
            return
          }

          if (style === "shield") {
            const shock = this.add.ellipse(impact.x, impact.y + 18, 48, 72, color, .18).setStrokeStyle(6, color, .9).setScale(.25).setDepth(36)
            const ram = this.add.rectangle(origin.x, origin.y + 20, 78, 12, color, .72).setRotation(Math.atan2(impact.y - origin.y, impact.x - origin.x)).setDepth(35)
            this.tweens.add({ targets: ram, x: impact.x, y: impact.y + 18, alpha: 0, duration, ease: "Cubic.In", onComplete: () => ram.destroy() })
            this.tweens.add({ targets: shock, scale: critical ? 2 : 1.55, alpha: 0, duration: duration + 100, ease: "Back.Out", onComplete: () => shock.destroy() })
            this.burst([target.id], color)
            onImpact()
            return
          }

          const projectileCount = special ? (style === "needle" ? 5 : style === "crossbow" || style === "spore" || style === "ice" ? 3 : 1) : style === "needle" ? 3 : 1
          for (let index = 0; index < projectileCount; index += 1) {
            const lane = (index - (projectileCount - 1) / 2) * 10
            const projectile = style === "crossbow" || style === "needle"
              ? this.add.rectangle(origin.x, origin.y + lane, style === "crossbow" ? 38 : 24, style === "crossbow" ? 5 : 3, color, .98).setDepth(36)
              : this.add.circle(origin.x, origin.y + lane, style === "spore" ? 11 : 8, color, .92).setStrokeStyle(3, 0xf5fbff, .72).setDepth(36)
            projectile.setRotation(Math.atan2(impact.y - (origin.y + lane), impact.x - origin.x))
            const tracer = this.add.rectangle(origin.x - direction * 18, origin.y + lane, style === "needle" ? 42 : 62, style === "needle" ? 2 : 4, color, .45).setRotation(projectile.rotation).setDepth(35)
            this.tweens.add({
              targets: projectile,
              x: impact.x,
              y: impact.y + lane * .25,
              delay: index * 28,
              duration: duration + index * 18,
              ease: "Cubic.In",
              onComplete: () => { projectile.destroy(); if (index === projectileCount - 1) { this.burst([target.id], color); onImpact() } },
            })
            this.tweens.add({ targets: tracer, x: impact.x - direction * 24, y: impact.y + lane * .25, alpha: 0, delay: index * 28, duration: duration + 45, onComplete: () => tracer.destroy() })
          }
        }

        private impactTarget(event: BattleEvent, actor: any, target: any, critical: boolean) {
          if (!target) return
          this.float([target.id], `-${Number(event.numeric_values?.amount || 0)}`, "#ff8796")
          this.flash([target.id], critical ? 0xffc36d : 0xff8796)
          if (this.compactMotion || target === actor || !target.alive) return
          this.interruptActionPose(target)
          const direction = Math.sign(target.baseX - actor.baseX) || (target.side === "ally" ? -1 : 1)
          const knockback = direction * (critical ? 26 : 18)
          this.tweens.add({
            targets: target.motionRoot,
            x: target.baseX + knockback,
            y: target.baseY + (critical ? 8 : 4),
            angle: direction * (critical ? 8 : 5),
            duration: this.motionMs(58, 28),
            ease: "Cubic.Out",
            onComplete: () => this.time.delayedCall(this.motionMs(critical ? 72 : 42, 20), () => {
              if (!target.alive) return
              this.tweens.add({
                targets: target.motionRoot,
                x: target.baseX - direction * 5,
                y: target.baseY - 2,
                angle: direction * -2,
                duration: this.motionMs(86, 36),
                ease: "Quad.Out",
                onComplete: () => this.tweens.add({ targets: target.motionRoot, x: target.baseX, y: target.baseY, angle: 0, duration: this.motionMs(90, 38), ease: "Sine.InOut", onComplete: () => target.alive && this.snapActionPose(target) }),
              })
            }),
          })
          this.tweens.add({ targets: target.shadow, scaleX: critical ? 1.25 : 1.14, alpha: .38, duration: this.motionMs(78, 35), yoyo: true, onComplete: () => target.alive && target.shadow.setScale(1).setAlpha(target.baseShadowAlpha) })
        }

        private animateHit(event: BattleEvent, critical: boolean, special: boolean, firstSkillResult: boolean) {
          const actor = this.actorViews.get(event.actor_id); if (!actor) return
          const targets = (event.target_ids || []).map(id => this.actorViews.get(id)).filter(Boolean)
          const firstTarget = targets[0]
          if (!firstTarget) return
          const style = this.attackStyle(actor)
          const isMelee = ["blade", "shield", "hammer"].includes(style)
          const runBody = actor.alive && (!special || firstSkillResult)
          let attackPoseToken = 0
          const strike = () => {
            for (const target of targets) this.emitWeaponFx(actor, target, critical, special, () => this.impactTarget(event, actor, target, critical))
          }
          if (this.compactMotion || !runBody) { strike(); return }

          this.interruptActionPose(actor)
          const direction = Math.sign(firstTarget.baseX - actor.baseX) || (actor.side === "ally" ? 1 : -1)
          if (!isMelee) {
            attackPoseToken = this.showActionPose(actor)
            this.tweens.add({
              targets: actor.motionRoot,
              x: actor.baseX - direction * 12,
              y: actor.baseY - 5,
              angle: direction * -3,
              duration: this.motionMs(145, 55),
              ease: "Quad.Out",
              onComplete: () => {
                strike()
                this.time.delayedCall(this.motionMs(150, 55), () => {
                  this.showIdlePose(actor, attackPoseToken)
                  if (!actor.alive) return
                  this.tweens.add({ targets: actor.motionRoot, x: actor.baseX, y: actor.baseY, angle: 0, duration: this.motionMs(150, 60), ease: "Sine.InOut", onComplete: () => actor.alive && this.snapActionPose(actor) })
                })
              },
            })
            return
          }

          const stopDistance = style === "blade" ? 72 : style === "hammer" ? 94 : 86
          const destinationX = firstTarget.baseX - direction * stopDistance
          const destinationY = firstTarget.baseY
          actor.motionRoot.setDepth(34)
          if (actor.image) {
            const afterimage = this.add.image(actor.baseX, actor.baseY, actor.image.texture.key).setOrigin(.5, 1)
              .setScale(actor.baseImageScaleX, actor.baseImageScaleY).setFlipX(actor.image.flipX).setAlpha(.2).setTint(actor.teamColor).setDepth(actor.baseDepth - 1)
            this.tweens.add({ targets: afterimage, x: actor.baseX - direction * 16, alpha: 0, duration: this.motionMs(260, 90), onComplete: () => afterimage.destroy() })
          }
          this.tweens.add({
            targets: actor.motionRoot,
            x: destinationX,
            y: destinationY - 4,
            angle: direction * 2,
            duration: this.motionMs(style === "hammer" ? 250 : 210, 75),
            ease: "Cubic.InOut",
            onComplete: () => {
              if (!actor.alive) return
              this.tweens.add({
                targets: actor.motionRoot,
                x: destinationX - direction * 10,
                y: destinationY - (style === "hammer" ? 18 : 14),
                angle: direction * (style === "blade" ? -13 : style === "hammer" ? -16 : -7),
                duration: this.motionMs(style === "hammer" ? 150 : 105, 45),
                ease: "Quad.Out",
                onComplete: () => {
                  if (!actor.alive) return
                  this.tweens.add({
                    targets: actor.motionRoot,
                    x: destinationX + direction * (style === "blade" ? 28 : 20),
                    y: destinationY + 3,
                    angle: direction * (style === "blade" ? 9 : style === "hammer" ? 11 : 5),
                    duration: this.motionMs(style === "hammer" ? 140 : 110, 38),
                    ease: "Cubic.In",
                    onStart: () => { attackPoseToken = this.showActionPose(actor); strike() },
                    onComplete: () => this.time.delayedCall(this.motionMs(critical || special ? 170 : 130, 38), () => {
                      if (!actor.alive) return
                      this.showIdlePose(actor, attackPoseToken)
                      this.tweens.add({
                        targets: actor.motionRoot,
                        x: actor.baseX,
                        y: actor.baseY,
                        angle: 0,
                        duration: this.motionMs(style === "hammer" ? 270 : 230, 85),
                        ease: "Cubic.InOut",
                        onComplete: () => { if (actor.alive) { this.snapActionPose(actor); actor.motionRoot.setDepth(actor.baseDepth) } },
                      })
                    }),
                  })
                },
              })
            },
          })
        }

        private burst(ids: string[], color: number) {
          for (const id of ids) { const view = this.actorViews.get(id); if (!view) continue; const burst = this.add.circle(view.baseX, view.baseY - 86, 26, color, .12).setStrokeStyle(4, color).setDepth(35); this.tweens.add({ targets: burst, scale: 1.9, alpha: 0, duration: this.compactMotion ? 50 : 210, onComplete: () => burst.destroy() }) }
        }

        private supportFx(ids: string[], color: number) {
          for (const id of ids) {
            const view = this.actorViews.get(id); if (!view) continue
            const ring = this.add.ellipse(view.baseX, view.baseY - 74, 96, 152, color, .08).setStrokeStyle(3, color).setDepth(35)
            const glyph = this.add.rectangle(view.baseX, view.baseY - 82, 12, 64, color, .72).setDepth(36)
            const glyphCross = this.add.rectangle(view.baseX, view.baseY - 82, 64, 12, color, .72).setDepth(36)
            this.tweens.add({ targets: ring, scale: 1.55, alpha: 0, duration: this.compactMotion ? 70 : 300, onComplete: () => ring.destroy() })
            this.tweens.add({ targets: [glyph, glyphCross], y: view.baseY - 118, scale: 1.25, alpha: 0, duration: this.compactMotion ? 70 : 310, onComplete: () => { glyph.destroy(); glyphCross.destroy() } })
            if (!this.compactMotion && view.alive) {
              this.interruptActionPose(view)
              this.tweens.add({ targets: view.motionRoot, y: view.baseY - 9, scaleX: 1.025, scaleY: 1.025, duration: 135, yoyo: true, ease: "Sine.Out", onComplete: () => view.alive && this.snapActionPose(view) })
            }
          }
        }

        private updateHp(id: string, next: number) {
          const view = this.actorViews.get(id); if (!view || !Number.isFinite(next)) return
          view.hp = Math.max(0, Math.min(view.maxHp, next))
          this.tweens.add({ targets: view.hpBar, scaleX: view.hp / Math.max(1, view.maxHp), duration: this.fastForward || this.compactMotion ? 0 : 140 })
        }

        private updateShield(id: string, delta: number) {
          const view = this.actorViews.get(id); if (!view) return
          view.shield = Math.max(0, Math.min(view.maxHp, view.shield + delta)); view.shieldBar.setScale(view.shield / Math.max(1, view.maxHp), 1)
        }

        private updateStatus(id: string, event: BattleEvent, visual: boolean) {
          const state = this.statusState.get(id); if (!state) return
          const statuses = event.status_ids || []
          for (const status of statuses) { if (event.event_type === "STATUS_REMOVE" || event.event_type === "CLEANSE") state.delete(status); else state.add(status) }
          const view = this.actorViews.get(id)
          view?.statusText?.setText([...state].slice(0, 2).map(status => statusLabel[status] || status).join(" · "))
          if (visual) this.float([id], statuses.length ? statuses.map(status => statusLabel[status] || status).join(" · ") : "状态", event.event_type === "STATUS_TICK" ? "#e0b56e" : "#caa8ff")
        }

        private float(ids: string[], message: string, color: string) {
          for (const id of ids) { const view = this.actorViews.get(id); if (!view) continue; const text = this.add.text(view.baseX, view.baseY - 150, message, { color, fontFamily: CN_DISPLAY_FONT, fontSize: "22px", fontStyle: "bold", stroke: "#07101d", strokeThickness: 6 }).setOrigin(.5).setDepth(36); this.tweens.add({ targets: text, y: view.baseY - 198, scale: 1.08, alpha: 0, duration: this.compactMotion ? 110 : 560, onComplete: () => text.destroy() }) }
        }

        private flash(ids: string[], color: number) {
          for (const id of ids) { const view = this.actorViews.get(id); if (!view) continue; view.image?.setTint(color); view.actionImage?.setTint(color); view.figure?.setAlpha(.52); view.aura.setFillStyle(color, .22); this.time.delayedCall(this.compactMotion ? 45 : 100, () => { if (view.alive) { view.image?.clearTint(); view.actionImage?.clearTint(); view.figure?.setAlpha(1); view.aura.setFillStyle(view.teamColor, .08) } }) }
        }

        private offset(ids: string[], amount: number) {
          for (const id of ids) {
            const view = this.actorViews.get(id); if (!view || !view.alive) continue
            const direction = view.side === "ally" ? -1 : 1
            this.interruptActionPose(view)
            this.tweens.add({ targets: view.motionRoot, x: view.baseX + amount * direction, y: view.baseY - 6, angle: direction * 4, duration: this.compactMotion ? 0 : 95, yoyo: true, ease: "Quad.Out", onComplete: () => view.alive && this.snapActionPose(view) })
          }
        }

        private death(id: string, visual: boolean) {
          const view = this.actorViews.get(id); if (!view) return
          this.interruptActionPose(view)
          view.alive = false
          view.idleTweens?.forEach((tween: any) => tween.pause())
          view.motionRoot.setPosition(view.baseX, view.baseY).setScale(1).setAngle(0).setAlpha(1)
          view.idleRoot.setPosition(0, 0).setScale(1).setAngle(0).setAlpha(1)
          view.image?.setVisible(!view.defeatedImage).setScale(view.baseImageScaleX, view.baseImageScaleY).setTint(0x59616f)
          view.actionImage?.setVisible(false)
          view.figure?.setVisible(!view.defeatedImage).setAlpha(.72)
          view.badge?.setVisible(!view.defeatedImage).setTint(0x59616f)
          view.defeatedImage?.setVisible(true).setScale(view.baseDefeatedScaleX, view.baseDefeatedScaleY).clearTint()
          view.aura.setFillStyle(0x59616f, .04)
          if (!visual || this.compactMotion) {
            view.defeatedImage?.setAlpha(.96)
            this.hudNodes(view).forEach((node: any) => node.setAlpha(.12))
            view.aura.setAlpha(.04)
            view.shadow.setScale(1.22, .78).setAlpha(.42)
            return
          }
          if (view.defeatedImage) {
            view.defeatedImage.setY(-18).setAlpha(0)
            this.tweens.add({ targets: view.defeatedImage, y: 0, alpha: .96, duration: 340, ease: "Cubic.Out" })
          } else {
            const direction = view.side === "ally" ? -1 : 1
            this.tweens.add({ targets: view.idleRoot, x: direction * 18, y: 3, angle: direction * 68, alpha: .82, duration: 390, ease: "Cubic.In" })
          }
          this.tweens.add({ targets: this.hudNodes(view), alpha: .12, duration: 250 })
          this.tweens.add({ targets: view.aura, alpha: .04, duration: 280 })
          this.tweens.add({ targets: view.shadow, scaleX: 1.22, scaleY: .78, alpha: .42, duration: 300, ease: "Cubic.Out" })
        }

        private showCutin(event: BattleEvent) {
          const presentation = resolveSkillPresentation(event, this.replayHeroes, this.replayUnits)
          const { unit, hero } = presentation
          const side = unit?.side === "enemy" ? "enemy" : "ally"
          const cutin = hero ? assetKeys(hero, "skill_cutin") : null
          const enemyKey = `expedition:enemy:${unit?.hero_id || ""}:battle`
          if (cutin || enemyBattlePortraitUrl(unit?.hero_id || "")) {
            const key = cutin ? (this.textures.exists(cutin.key) ? cutin.key : cutin.fallbackKey) : enemyKey
            const imageX = side === "ally" ? 275 : 805
            const imageStartX = imageX + (side === "ally" ? -42 : 42)
            this.cutin.image.setTexture(key).setPosition(imageStartX, BATTLE_HEIGHT / 2)
            const imageScale = Math.min((side === "ally" ? 440 : 360) / Math.max(1, this.cutin.image.width), 260 / Math.max(1, this.cutin.image.height))
            this.cutin.image.setScale(imageScale)
            this.cutin.image.setFlipX(shouldFlipPortrait(side, unit?.hero_id || hero?.hero_id || ""))
            this.cutin.image.setVisible(true).setAlpha(0)
          } else {
            this.cutin.image.setVisible(false)
          }
          const copyX = side === "ally" ? 585 : 85
          const cutinY = BATTLE_HEIGHT / 2
          this.cutin.caption.setPosition(copyX, cutinY - 91).setText(side === "ally" ? "奥义发动" : "敌技发动")
          this.cutin.text.setPosition(copyX, cutinY - 37).setText(presentation.name)
          this.cutin.summary.setPosition(copyX + 3, cutinY + 26).setText(presentation.summary)
          this.cutin.actor.setPosition(copyX + 3, cutinY + 82).setText(`施术者 · ${hero?.name || unit?.name || event.actor_id}`)
          this.cutin.seal.setPosition(side === "ally" ? 1010 : 70, cutinY + 95)
          this.cutin.sealText.setPosition(side === "ally" ? 1010 : 70, cutinY + 95)
          this.cutin.wash.setFillStyle(side === "ally" ? 0x321b16 : 0x25162d, .52)
          for (const node of this.cutin.nodes) node.setVisible(true).setAlpha(node === this.cutin.image ? 0 : 1)
          this.cutin.panel.setScale(.9, 1)
          this.cutin.text.setScale(.9)
          if (!this.compactMotion) {
            this.cameras.main.shake(90, .0012)
            this.tweens.add({ targets: this.cutin.panel, scaleX: 1, duration: 190, ease: "Cubic.Out" })
            this.tweens.add({ targets: this.cutin.image, alpha: 1, x: side === "ally" ? 275 : 805, duration: 240, ease: "Cubic.Out" })
            this.tweens.add({ targets: this.cutin.text, scale: 1, duration: 260, ease: "Back.Out" })
          } else {
            this.cutin.panel.setScale(1, 1); this.cutin.image.setPosition(side === "ally" ? 275 : 805, BATTLE_HEIGHT / 2).setAlpha(1); this.cutin.text.setScale(1)
          }
          const hold = Math.max(360, this.eventDuration(event) - 240)
          this.time.delayedCall(hold, () => this.tweens.add({ targets: this.cutin.nodes, alpha: 0, duration: this.compactMotion ? 0 : 180, onComplete: () => this.cutin.nodes.forEach((node: any) => node.setVisible(false).setAlpha(1)) }))
        }
      }

      const scene = new ExpeditionBattleScene()
      scene.configure(events, units, sceneHeroes, speed, reducedMotion, (event: BattleEvent, index: number) => setCursor(index), warning => setLoadWarning(warning))
      if (paused) scene.setReplayPaused(true)
      sceneRef.current = scene
      game = new Phaser.Game({ type: Phaser.CANVAS, width: BATTLE_WIDTH, height: BATTLE_HEIGHT, parent: mountRef.current, transparent: true, render: { antialias: true, roundPixels: true }, scene })
    }).catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : String(error || "unknown error")
      console.error("[MentorFlow] Phaser battle replay failed to initialize", error)
      setLoadWarning(`战斗画布初始化失败：${detail}`)
    })

    return () => { disposed = true; sceneRef.current = null; game?.destroy(true) }
  }, [events, units, heroes])

  useEffect(() => { sceneRef.current?.setSpeed(speed) }, [speed])
  useEffect(() => { sceneRef.current?.setReducedMotion(reducedMotion) }, [reducedMotion])
  useEffect(() => { sceneRef.current?.setReplayPaused(paused) }, [paused])

  const latest = cursor >= 0 ? events[cursor] : undefined
  const latestSkill = latest?.event_type === "SKILL_CAST" ? resolveSkillPresentation(latest, heroes, units) : undefined
  return <section className="expedition-replay" aria-label="战斗回放">
    <div className="expedition-replay-head"><div><span className="expedition-kicker">BATTLE REPLAY · STAGE {stageNumber || "—"}</span><h3>{winner === "ally" ? "胜利回放" : winner ? "战斗记录" : "等待事件"}</h3></div><div className="expedition-speed" role="group" aria-label="回放速度">{[1, 2, 4].map(value => <button key={value} className={speed === value ? "active" : ""} onClick={() => setSpeed(value as 1 | 2 | 4)}>{value}x</button>)}</div></div>
    <div className="expedition-replay-loading"><span>素材预加载 {loadProgress}%</span>{loadWarning && <b role="status">{loadWarning}</b>}</div>
    <div className="expedition-replay-stage" ref={mountRef} />
    <div className="expedition-replay-controls"><button className="expedition-small-button" onClick={() => setPaused(value => !value)}>{paused ? "继续" : "暂停"}</button><button className="expedition-small-button" onClick={() => sceneRef.current?.restartReplay()}>重播</button><button className="expedition-small-button" onClick={() => sceneRef.current?.skipReplay()}>跳过</button><label><input type="checkbox" checked={reducedMotion} onChange={event => setReducedMotion(event.target.checked)} /> 减少动画</label><span>{cursor + 1 > 0 ? `${cursor + 1} / ${events.length}` : `${events.length} 个事件`}</span><b>{latest ? (latestSkill ? `${latestSkill.hero?.name || latestSkill.unit?.name || "角色"} · ${latestSkill.name}` : `${eventLabel[latest.event_type] || latest.event_type} · ${latest.actor_hero_id || latest.actor_id}`) : "事件将按后端顺序播放"}</b></div>
    <div className="expedition-event-strip">{events.slice(Math.max(0, cursor - 2), cursor + 4).map(event => <span key={event.event_index} className={event.event_index === latest?.event_index ? "current" : ""}>{eventLabel[event.event_type] || event.event_type}</span>)}</div>
  </section>
}
