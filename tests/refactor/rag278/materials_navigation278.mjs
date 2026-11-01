// Layout-only adapter for existing RAG business browser scenarios. Every
// navigation action uses their real CDP click, just as a user opens the drawer.
export async function openMaterials278(evaluate, click) {
  if (await evaluate(`!!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`)) return;
  await click('[data-testid="rag-materials-open"]');
  await waitMaterials278(evaluate, true);
}

export async function closeMaterials278(evaluate, click) {
  if (!await evaluate(`!!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`)) return;
  await click('[data-testid="rag-materials-close"]');
  await waitMaterials278(evaluate, false);
}

async function waitMaterials278(evaluate, expected) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(`!!document.querySelector('[data-testid="rag-materials-dialog"]')?.open`) === expected) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw Error(`materials window did not ${expected ? 'open' : 'close'}`);
}

export async function reachRagTarget278(evaluate, click, selector) {
  const inside = await evaluate(`(()=>{const target=document.querySelector(${JSON.stringify(selector)});return !!target?.closest('[data-testid="rag-materials-dialog"]')})()`);
  if (inside) await openMaterials278(evaluate, click);
  else await closeMaterials278(evaluate, click);
}
