# amd64 is required for both the Windows compiler and the shipped frozen bytecode.
FROM python:3.12.10-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive WINEARCH=win64 WINEPREFIX=/opt/wine \
    WINEDEBUG=-all WINEDLLOVERRIDES=mscoree,mshtml= \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      wine wine64 xvfb xauth ca-certificates curl fonts-wine \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir \
      PyInstaller==6.16.0 Pillow==12.3.0 cryptography==46.0.7

# Official x64 installer; checksum also published in Microsoft's winget manifest.
RUN curl --fail --location --retry 3 \
      https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe \
      --output /tmp/inno.exe \
    && echo '0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f  /tmp/inno.exe' | sha256sum --check - \
    && xvfb-run -a wine /tmp/inno.exe /SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ALLUSERS '/DIR=C:\InnoSetup' \
    && wineserver -w \
    && test -f /opt/wine/drive_c/InnoSetup/ISCC.exe \
    && test -f /opt/wine/drive_c/InnoSetup/Languages/ChineseSimplified.isl \
    && xvfb-run -a wine reg add 'HKCU\Software\Wine' /v ShowDotFiles /d Y /f \
    && wineserver -w \
    && rm /tmp/inno.exe

WORKDIR /opt/mentorflow-installer
# Build context is the small bundle made by make_bundle.py, never the full payload.
COPY . .
ENTRYPOINT ["xvfb-run", "-a", "python", "-B", "scripts/server_installer/run_build.py"]
