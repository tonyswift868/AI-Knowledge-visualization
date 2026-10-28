import { call } from "./bridge"
import { useApp, type Settings, type SettingsSnapshot } from "./store"

/** Save settings through the shared per-account snapshot and optimistic-update guard. */
export async function saveSettings(changes: Partial<Settings>): Promise<boolean> {
  const ticket = useApp.getState().beginSettingsSave(changes)
  try {
    const snapshot = await call<SettingsSnapshot>("settings.save", { changes })
    return useApp.getState().finishSettingsSave(ticket, snapshot)
  } catch (error) {
    useApp.getState().failSettingsSave(ticket)
    const current = useApp.getState()
    if (current.settingsScopeRevision !== ticket.scope || (ticket.account_id && current.account?.account_id !== ticket.account_id)) return false
    throw error
  }
}
