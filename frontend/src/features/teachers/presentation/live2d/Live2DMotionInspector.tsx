import type { Live2DModelCapabilities } from "./live2dCapabilities"
import type { Live2DSemanticAction } from "./live2dMotionRegistry"
import "./live2dMotionInspector.css"

export type Live2DMotionInspectorProps = {
  capabilities?: Live2DModelCapabilities
  actions: readonly Live2DSemanticAction[]
  enabled?: boolean
  onTrigger: (actionId: string) => void
  onStop?: () => void
  onFreeze?: () => void
  onResume?: () => void
  onRestore: () => void
}

/**
 * A deliberately opt-in developer surface.  It is rendered only when the
 * existing caller enables debug/action testing, so a classroom never gets a
 * permanent motion panel or a fake canvas preview.
 */
export function Live2DMotionInspector({
  capabilities,
  actions,
  enabled = false,
  onTrigger,
  onStop,
  onFreeze,
  onResume,
  onRestore,
}: Live2DMotionInspectorProps) {
  if (!enabled) return null
  return (
    <details data-testid="live2d-motion-inspector" className="live2d-motion-inspector">
      <summary>Live2D Motion Inspector</summary>
      <h4>Model</h4>
      <div role="group" aria-label="Live2D capability summary">
        <span>Parameters: {capabilities?.parameters.length ?? 0}</span>
        <span>Native motions: {Object.values(capabilities?.motionGroups || {}).reduce((count, group) => count + group.length, 0)}</span>
        <span>Expressions: {capabilities?.expressions.length ?? 0}</span>
      </div>
      <h4>Native Motions</h4>
      <ul aria-label="Native motions">
        {Object.entries(capabilities?.motionGroups || {}).flatMap(([group, motions]) => motions.map(motion => <li key={`${group}:${motion.index}`}>{group}:{motion.index}</li>))}
      </ul>
      <h4>Imported OSS</h4>
      <p>None imported; this inspector does not copy model assets.</p>
      <h4>Retargeted</h4>
      <p>Internal-parameter actions are marked as retargeted and are filtered against physics outputs.</p>
      <h4>Expressions</h4>
      <ul aria-label="Expressions">
        {(capabilities?.expressions || []).map(expression => <li key={expression.name}>{expression.name}</li>)}
      </ul>
      <h4>Parameters</h4>
      <ul aria-label="Safe parameters">
        {(capabilities?.parameters || []).map(parameter => <li key={parameter.id}>{parameter.id}</li>)}
      </ul>
      <h4>Semantic Actions</h4>
      <div role="list" aria-label="Semantic actions">
        {actions.map(action => (
          <div key={action.id} role="listitem">
            <button
              type="button"
              data-action-id={action.id}
              data-action-source={action.sourceLicense}
              data-action-implementation={action.implementation}
              data-action-parameters={action.requiredParameters.join(",")}
              data-action-supported={action.supported === false ? "false" : "true"}
              data-action-reason={action.supportReason || ""}
              data-action-preview={action.previewRecommended ? "true" : "false"}
              title={`${action.sourceLicense}/${action.implementation}`}
              disabled={action.supported === false}
              onClick={() => onTrigger(action.id)}
            >
              Play {action.label}
            </button>
          </div>
        ))}
      </div>
      <div>
        {onStop && <button type="button" onClick={onStop}>Stop</button>}
        {onFreeze && <button type="button" onClick={onFreeze}>Freeze</button>}
        {onResume && <button type="button" onClick={onResume}>Resume</button>}
        <button type="button" onClick={onRestore}>Restore authoritative state</button>
      </div>
    </details>
  )
}
