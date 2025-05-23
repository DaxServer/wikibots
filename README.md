# Deploying on Toolforge

## Building

`-L` flag uses the latest versions of the buildpacks, which is required to support poetry.

For main branch:

```bash
toolforge build start -L -i wikibots https://github.com/DaxServer/wikibots.git
```

For a specific branch:

```bash
toolforge build start -L -i wikibots-pr-10 https://github.com/DaxServer/wikibots.git --ref <BRANCH>
```

## Deploying

Use `<COMMAND>` from Procfile

For a one-off run:

```bash
toolforge jobs run --image tool-curator/wikibots:latest --emails all --filelog --mount all --command <COMMAND> <NAME>
```

For continuous run:

```bash
toolforge jobs run --image tool-curator/wikibots:latest --emails all --continuous --filelog --mount all --command <COMMAND> <NAME>
```

# ToDo

## Commons

1. Add gbif to iNaturalist images


## OpenRefine

1. Denkmalatlas Niedersachsen https://commons.wikimedia.org/wiki/Commons:Batch_uploading/Denkmalatlas_Niedersachsen
2. APPLAUSE https://commons.wikimedia.org/wiki/Commons:Batch_uploading/APPLAUSE
