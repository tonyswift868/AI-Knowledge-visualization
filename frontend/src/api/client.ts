export async function apiRequest<T>(path:string, init?:RequestInit):Promise<T>{
  const response=await fetch('/api'+path,{headers:{'Content-Type':'application/json',...(init?.headers||{})},...init})
  const data=await response.json()
  if(!response.ok) throw new Error(data.error?.message || data.detail?.error?.message || '请求失败')
  return data as T
}
export async function uploadFiles(path:string, files:File[]):Promise<void>{
  for(const file of files){const form=new FormData();form.append('file',file);const r=await fetch('/api'+path,{method:'POST',body:form});if(!r.ok)throw new Error(`上传失败：${file.name}`)}
}
