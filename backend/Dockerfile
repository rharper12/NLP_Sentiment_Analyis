# Lambda container image. Built with the repository root as context (see DockerContext in the
# template), so paths below are repo-relative even though this file lives in backend/.
# A container is used rather than a zip so the NLTK corpora and pyarrow fit comfortably and are
# baked in at build time: nothing downloads at runtime.
FROM public.ecr.aws/lambda/python:3.12

ENV NLTK_DATA=/usr/share/nltk_data \
    RUNTIME=lambda

COPY backend/pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY backend/src/ ${LAMBDA_TASK_ROOT}/src/

RUN pip install --no-cache-dir ${LAMBDA_TASK_ROOT} \
 && python -c "from sqlalchemy import create_engine; import psycopg; create_engine('postgresql+psycopg://user:pass@localhost/db')" \
 && python -m nltk.downloader -d ${NLTK_DATA} wordnet omw-1.4 averaged_perceptron_tagger_eng

CMD ["sentiment_prep.api.app.handler"]
