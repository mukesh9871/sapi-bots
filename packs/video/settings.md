# Settings: "<key> | folder|file|text|longtext|secret | <label> [| required, chat]" (chat: the set-up role may fill it in)
comfy_url | text | ComfyUI address on this PC (default http://127.0.0.1:8188)
templates_folder | folder | Folder with your ComfyUI workflow templates (API format .json, with {{placeholders}}, saving .mp4 or .webm) | required
ffmpeg_path | text | Full path of ffmpeg.exe (leave empty if ffmpeg is on PATH)
