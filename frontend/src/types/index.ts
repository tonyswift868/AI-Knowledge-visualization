export type GroundingStatus = 'grounded' | 'supplemental' | 'insufficient' | 'conflict' | 'procedural_guidance'
export interface Citation { source_id:string; source_title:string; chunk_id:string; page_start?:number|null; page_end?:number|null; heading?:string; quote:string; trust_level?:string; status?:GroundingStatus }
export interface ApiErrorShape { error:{ code:string; message:string; details:Record<string,unknown>; request_id:string } }
