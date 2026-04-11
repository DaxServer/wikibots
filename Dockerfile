FROM python:3.13.13-alpine3.23

RUN pip install poetry

WORKDIR /app
COPY . .

ENTRYPOINT [ "poetry", "run" ]
