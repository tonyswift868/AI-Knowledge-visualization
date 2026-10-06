from tests.refactor.plan51.test_plan51_selected_material_flow import flow, URL


def test_plan61_preview_text_lists_modules_without_expanded_chapters(flow):
    f, ctx, pid, _ = flow
    f.run(pid, lambda d, c: f.chat(d, c, URL), ctx)
    draft = f.run(pid, lambda d, c: f.make_outline(d, c), ctx)['draft']
    structure = draft['material_structure']
    message = draft['messages'][-1]
    assert message['version'] == structure['version']
    assert message['text'].startswith(f"已按资料整理出 {len(structure['modules'])} 个模块：")
    assert '章节：' not in message['text']
    for n, module in enumerate(structure['modules'], 1):
        assert f"{n}. {module['title']}" in message['text']
        assert module['chapters']  # All references remain in the structured payload.
    assert '所选资料没讲到：' in message['text'] if structure['missing'] else True
