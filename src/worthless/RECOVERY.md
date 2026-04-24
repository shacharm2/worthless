# Recovering a corrupted `.env`

If `worthless lock` left your `.env` file looking wrong, or you edited
it by hand and want the previous bytes back, use the restore command.

## Recover

```shell
worthless restore <path-to-env-file>
```

That's it. `worthless` keeps up to 50 backups of each protected file
under `$XDG_DATA_HOME/worthless/backups/` (defaulting to
`~/.local/share/worthless/backups/`) and restores the newest one.

## Troubleshooting

- Backups are local-disk only: network filesystems (NFS, CIFS) may
  silently drop the durability barrier, so treat them as best-effort.
