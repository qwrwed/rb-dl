Usage:

- Update config (e.g. `config/app.yaml`)
  - Set `base_dir` in your config file
  - Set [`api_key`](https://rebrickable.com/api/) in your config file
- To prompt for URLs:
  - `uv run rbdl`
- To get individual MOC(s):
  - `uv run rbdl "https://rebrickable.com/mocs/MOC-204100/marinbrickdesign/lego-christmas-english-phone-booth-instructions-lego-winter-village-moc"`
- To get bulk MOC(s):
  - `uv run rbdl "https://rebrickable.com/users/PiXEL-DAN/mocs/"`
  - `uv run rbdl "https://rebrickable.com/users/PiXEL-DAN/mocs/?theme=171"`
- To get official set(s):
  - `uv run rbdl "https://rebrickable.com/sets/75419-1/death-star"`
