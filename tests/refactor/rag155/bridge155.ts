/** Test fixture contract for the local RAG bridge. No network or model calls. */
export const seed155 = {
  plans: [
    { id: 'plan-a', title: '计算机入门', document_ids: ['doc-a', 'doc-b'] },
    { id: 'plan-b', title: '人工智能基础', document_ids: ['doc-c'] },
  ],
  documents: [
    { id: 'doc-a', title: '计算机基础.md', source: '上传资料', plan_ids: ['plan-a'], status: 'ready', total_chunks: 2, uploaded_chunks: 2 },
    { id: 'doc-b', title: '网络概念.md', source: '上传资料', plan_ids: ['plan-a'], status: 'not_uploaded', total_chunks: 3, uploaded_chunks: 0 },
    { id: 'doc-c', title: '机器学习.md', source: '上传资料', plan_ids: ['plan-b'], status: 'ready', total_chunks: 1, uploaded_chunks: 1 },
  ],
}

export const passages155 = [
  { number: 1, document_id: 'doc-a', seq: 2, text: '向量检索会把文字转换成向量，再按语义相似度寻找相关段落。', page: 4, section: '向量检索', title: '计算机基础.md' },
  { number: 2, document_id: 'doc-b', seq: 1, text: '检索结果应结合来源核对，避免把相似内容当成原文结论。', page: 2, section: '资料核对', title: '网络概念.md' },
]
