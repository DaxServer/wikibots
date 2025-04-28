# Deploying on Toolforge

## Building

For main branch:

```bash
toolforge build start -i <IMAGE-NAME> https://github.com/DaxServer/wikibots.git
```

For a specific branch:

```bash
toolforge build start -i <IMAGE-NAME> https://github.com/DaxServer/wikibots.git --ref <BRANCH>
```

## Deploying

Use `<COMMAND>` from Procfile

For a one-off run:

```bash
toolforge jobs run --image tool-curator/<IMAGE-NAME>:latest --command <COMMAND> <NAME>
```

For continuous run:

```bash
toolforge jobs run --continuous --image tool-curator/<IMAGE-NAME>:latest --command <COMMAND> <NAME>
```

# ToDo

## Commons

1. Add YouTube SDC to videos
2. Add Flickr SDC to images
3. Add iNaturalist SDC to images
   1. Add gbif to iNaturalist images


## OpenRefine

1. Denkmalatlas Niedersachsen https://commons.wikimedia.org/wiki/Commons:Batch_uploading/Denkmalatlas_Niedersachsen
2. Modern Sketch https://commons.wikimedia.org/wiki/Commons:Batch_uploading/Modern_Sketch
3. OpenUp RBINS Beetles collection https://commons.wikimedia.org/wiki/Commons:Batch_uploading/OpenUp_RBINS_Beetles_collection
4. APPLAUSE https://commons.wikimedia.org/wiki/Commons:Batch_uploading/APPLAUSE
