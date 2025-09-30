FROM python:3.13.7-alpine3.22

RUN pip install poetry

WORKDIR /app
COPY . .

RUN poetry install

ENTRYPOINT [ "poetry", "run", "pas" ]
