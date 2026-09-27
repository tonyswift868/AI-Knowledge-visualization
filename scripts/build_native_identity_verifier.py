"""Compile the identity SPI against an exact local Keycloak 26.7.3 distribution."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy/enterprise/native-identity-verifier/NativeIdentityVerifier.java'


def build(keycloak: Path, jdk: Path, output: Path):
    libs = keycloak / 'lib/lib/main'
    if not (libs / 'org.keycloak.keycloak-core-26.7.3.jar').is_file():
        raise ValueError('Exact Keycloak 26.7.3 is required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='native-spi-', dir=output.parent) as temp:
        javac = jdk / 'bin' / ('javac.exe' if (jdk / 'bin/javac.exe').exists() else 'javac')
        sources = [SOURCE, *(SOURCE.parent / name for name in (
            'AtomicActionTokens.java', 'AtomicVerifyEmailActionTokenHandler.java',
            'AtomicResetCredentialsActionTokenHandler.java', 'AtomicResetPassword.java', 'ResetCompletions.java', 'ObservedExecuteActions.java', 'EmailCodeReset.java', 'EmailCodeBinding.java', 'MailTransportHealth.java', 'RegistrationCodes.java'))]
        subprocess.run([str(javac), '--release', '21', '-cp', str(libs / '*'), '-d', temp,
                        *(str(source) for source in sources)], check=True)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as jar:
            for file in sorted(Path(temp).rglob('*.class')):
                jar.writestr(file.relative_to(temp).as_posix(), file.read_bytes())
            jar.writestr('META-INF/services/org.keycloak.services.resource.RealmResourceProviderFactory',
                         'win.clannad.mentorflow.NativeIdentityVerifier\n')
            jar.writestr('META-INF/services/org.keycloak.authentication.actiontoken.ActionTokenHandlerFactory',
                         'win.clannad.mentorflow.AtomicVerifyEmailActionTokenHandler\n'
                         'win.clannad.mentorflow.AtomicResetCredentialsActionTokenHandler\n'
                         'win.clannad.mentorflow.ObservedExecuteActions\n')
            jar.writestr('META-INF/services/org.keycloak.authentication.RequiredActionFactory',
                         'win.clannad.mentorflow.AtomicResetPassword\n')
            jar.writestr('META-INF/beans.xml', '<beans xmlns="https://jakarta.ee/xml/ns/jakartaee" bean-discovery-mode="all"/>')
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--keycloak', type=Path, required=True)
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.keycloak, args.jdk, args.output))
