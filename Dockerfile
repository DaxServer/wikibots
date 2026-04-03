FROM python:3.13.12-alpine3.23

RUN pip install poetry

WORKDIR /app
COPY . .

RUN poetry install

ENTRYPOINT [ "poetry", "run" ]
