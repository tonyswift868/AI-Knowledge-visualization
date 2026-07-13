import type { GroundingStatus } from '../types'
export function GroundingBadge({status}:{status:GroundingStatus}){return <span className={`grounding-badge grounding-${status}`}>{status}</span>}
