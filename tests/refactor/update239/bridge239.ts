export const fixture239: any = {
  status: {available:true,busy:false,state:'OPTIONAL_UPDATE',version:'2.0.37',current:'2.0.37',latest:'2.0.38',target:'2.0.38',release_sha256:'a'.repeat(64),message:'发现新版本'},
  calls: [] as string[],
}
export const on = () => () => undefined
export async function connect() { return true }
export async function call(method:string) {
  fixture239.calls.push(method)
  if (method === 'update.status') return {...fixture239.status}
  if (method === 'update.automatic' || method === 'update.check') return {...fixture239.status}
  if (method === 'update.install') return {...fixture239.status,busy:true,state:'DOWNLOADING',download_state:'PREPARING'}
  if (method === 'update.defer') return {...fixture239.status,deferred:true}
  return {ok:true}
}
