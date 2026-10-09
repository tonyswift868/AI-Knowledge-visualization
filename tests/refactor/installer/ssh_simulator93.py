"""Local subprocess stand-in for SSH/SCP; never opens a network connection.

Runs the actual stdin program after mapping server paths into a test directory.
It deliberately accepts ONLY the fixed remote command, not arbitrary commands.
"""
import ast
import json
from pathlib import Path
import shutil
import sys


def main():
    root, mode = Path(sys.argv[1]), sys.argv[2]
    root.mkdir(parents=True, exist_ok=True)
    arguments = sys.argv[3:]
    evidence = root / 'transport.jsonl'

    def record(**values):
        with evidence.open('a', encoding='utf-8') as out:
            out.write(json.dumps(dict(mode=mode, **values), ensure_ascii=False) + '\n')

    if mode == 'scp':
        source, destination = arguments[-2:]
        remote = destination.split(':', 1)[1]
        assert remote.startswith('/tmp/mf-release-')
        target = root / 'transfers' / Path(remote).name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        record(bytes=target.stat().st_size, target=str(target))
        return
    assert mode == 'ssh'
    assert arguments[-1] == 'sudo -n python3 -'
    program = sys.stdin.read()
    tree = ast.parse(program)
    assignment = tree.body[1]
    assert isinstance(assignment, ast.Assign)
    request_literal = assignment.value.args[0]
    request = json.loads(ast.literal_eval(request_literal))
    record(command=arguments[-1], stdin_bytes=len(program.encode('utf-8')),
           action=request['action'], version=request['version'])
    request['app'], request['state'] = str(root / 'app'), str(root / 'state')
    if 'transfer' in request:
        request['transfer'] = str(root / 'transfers' / Path(request['transfer']).name)
    assignment.value.args[0] = ast.Constant(json.dumps(request))
    # Compile the transported wrapper, which compiles and executes server_ops.
    # Only the destination paths above differ from the real remote operation.
    exec(compile(ast.fix_missing_locations(tree), '<local-ssh-stdin>', 'exec'),
         {'__name__': '__main__'})


if __name__ == '__main__':
    main()
