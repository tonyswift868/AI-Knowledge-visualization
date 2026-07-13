# MentorFlow V6 Secure ZIP Import Evidence

- Limits: ZIP 100 MB; expanded total 500 MB; single file 50 MB; 2000 members; depth 12; single ratio 100:1; overall ratio 50:1.
- Security: Zip Slip, absolute/UNC/drive paths, symlink/special files, encrypted ZIP, suspicious compression ratios, executable extensions and binary content are guarded.
- Flow: preview stores the archive under `data/archive_previews/<preview_id>/`; import revalidates archive hash/mtime and selected member hashes, extracts only selected files, parses independently, deduplicates by workspace hash, and cleans temporary files.
- Formats: PDF/DOCX/Markdown/TXT/HTML plus code and structured text; code is text-only; notebooks extract markdown/code cells without execution; nested archives are ignored.
- Traceability: Sources retain `archive_preview_id`, `parent_archive_name`, `archive_relative_path`, and language; titles and plan evidence include `archive / internal/path`.
- Results: focused backend exit `0`, ZIP E2E exit `0`, build exit `0`.
