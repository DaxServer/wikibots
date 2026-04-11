FROM python:3.13.13-alpine3.23

RUN pip install poetry

WORKDIR /app
COPY . .

RUN poetry install

ENTRYPOINT [ "poetry", "run" ]
