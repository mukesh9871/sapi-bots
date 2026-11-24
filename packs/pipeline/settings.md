# Settings: "<key> | folder|file|text|longtext|secret | <label> [| required, chat]" (chat: the set-up role may fill it in)
channels | longtext | Channel repos, one block per channel (the format is in the description above) | required
max_parallel | text | Most runs at once across all channels (default 3; GPU runs are always one at a time) | chat
