# Bulk drawing and DXF import

Planners and admins open **Parts & drawings → Import**. Download the CSV template and fill in the part number, revision, description, drawing_file and optional dxf_file. UTF-8 CSV headers may be reordered and surrounding header whitespace is ignored. New revisions require a PDF; existing revisions can receive their first DXF.

Choose multiple files or **Folder of drawings and DXFs** to include nested folders recursively. The browser only reads the folder you select; this is not a search of your entire computer or server. If your browser does not offer folder selection, use the multiple-file picker.

CSV references may be exact uploaded paths, paths below the selected folder (such as `Doors/BRKT-100_A.pdf`), or unique basenames. If two subfolders contain `drawing.pdf`, use their relative paths. Ambiguous references are rejected, never guessed. Selected files not mapped in the CSV are excluded and counted in the preview, for both folder and multiple-file selections. They no longer reject an otherwise valid batch. With JavaScript enabled, unrelated non-PDF/DXF folder files are excluded from the upload.

Limits remain 200 CSV rows, 400 PDF/DXF files, 10 MB per PDF, 20 MB per DXF and 50 MB per selection. Split larger libraries into smaller batches. Preview shows the resolved file paths and proposed actions. Confirm imports the batch atomically; conflicting files are never silently replaced. Error feedback explains the correction; after an error browsers require you to select the files again. Preview expiration, tenant ownership, duplicate protection and retention choices remain in effect.

Part numbers support slashes (for example `STRIKE-DA-1/8-SS`) throughout the catalog and request workflow. Filename matching tries exact paths first, then a unique case-insensitive match, so `.DXF` and `.dxf` can match without guessing between duplicate files. The selector shows separate PDF and DXF counts. A populated dxf_file cell requires the actual file to be selected; leave it blank for a PDF-only import and attach the DXF later.
