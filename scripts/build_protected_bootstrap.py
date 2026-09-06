"""Compile the elevated outer installer from verified payload and fixed pins."""
import json
from pathlib import Path
import subprocess
from scripts.prepare_bootstrap_payload import prepare
from scripts import verify_windows_package as v


def build(payload, output, **identities):
    prepared = prepare(payload, output, **identities)
    sources = Path(__file__).resolve().parents[1] / 'installer'
    executable = Path(output) / 'MentorFlow-Protected-Setup.exe'
    compiler = Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    command = [str(compiler), '/nologo', '/target:winexe', '/platform:x64',
               '/reference:System.IO.Compression.dll', '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
               '/win32manifest:' + str(sources / 'protected-bootstrap.manifest'),
               '/out:' + str(executable), '/resource:' + prepared['archive'] + ',MentorFlow.Payload',
               prepared['identity_source']]
    command += [str(sources / name) for name in ('ProtectedStage.cs', 'VerifiedPayload.cs', 'InstallFromEmbedded.cs', 'BootstrapWindow.cs')]
    subprocess.run(command, check=True)
    return dict(prepared, executable=str(executable), executable_sha256=v.sha256_file(executable),
                authenticode='UNSIGNED', requires_elevation=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--payload-root', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    for name in ('manifest-sha256', 'source-sha', 'tree-sha'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.payload_root, args.output_root, manifest_sha256=args.manifest_sha256,
                           source_sha=args.source_sha, tree_sha=args.tree_sha)))


if __name__ == '__main__':
    main()
