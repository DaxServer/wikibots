# Deploying on Toolforge

## Building

For main branch:

```bash
toolforge build start --image <IMAGE-NAME> https://github.com/DaxServer/wikibots.git
```

For a specific branch:

```bash
toolforge build start --image <IMAGE-NAME> https://github.com/DaxServer/wikibots.git --ref <BRANCH>
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

1. USACE SDC
2. Add YouTube SDC to videos
3. Add Flickr SDC to images
4. Add iNaturalist SDC to images
   1. Add gbif to iNaturalist images


## OpenRefine

1. Denkmalatlas Niedersachsen https://commons.wikimedia.org/wiki/Commons:Batch_uploading/Denkmalatlas_Niedersachsen
2. Perry–Castañeda Library Map Collection https://commons.wikimedia.org/wiki/Commons:Batch_uploading/Perry%E2%80%93Casta%C3%B1eda_Library_Map_Collection
3. Modern Sketch https://commons.wikimedia.org/wiki/Commons:Batch_uploading/Modern_Sketch
4. OpenUp RBINS Beetles collection https://commons.wikimedia.org/wiki/Commons:Batch_uploading/OpenUp_RBINS_Beetles_collection
5. GeoDIL https://commons.wikimedia.org/wiki/Commons:Batch_uploading/GeoDIL
6. APPLAUSE https://commons.wikimedia.org/wiki/Commons:Batch_uploading/APPLAUSE
