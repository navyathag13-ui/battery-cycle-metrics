# Lambda container image: numpy, scipy, pandas and pyarrow together are too big for a zip package.
# Python 3.12 (Amazon Linux 2023): the 3.11 image is Amazon Linux 2, whose glibc is too old for
# current numpy wheels, so pip tried to compile from source. --only-binary makes that fail fast.
# Build from the repo root:  docker build --platform linux/amd64 -f aws/lambda/Dockerfile .
FROM public.ecr.aws/lambda/python:3.12
COPY aws/lambda/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --only-binary=:all: -r ${LAMBDA_TASK_ROOT}/requirements.txt
COPY battery_metrics ${LAMBDA_TASK_ROOT}/battery_metrics
COPY aws/schema.json ${LAMBDA_TASK_ROOT}/aws/schema.json
COPY aws/lambda/app.py ${LAMBDA_TASK_ROOT}/
CMD ["app.handler"]
