-- wifiguard-db EC2 · postgres:16 컨테이너(wg-pg, 호스트 5432) 에 1회 적용.
-- 관계형 스키마(계정·시설·기기·거주자·이벤트 이력)는 packages/db Alembic(F-B11) 도입 후 관리한다.
-- compose/docker-compose.dev.yml:95-99 의 mlflow 서비스가 postgres/mlflow DB를 가정하므로 미리 만든다(현재 미사용).
SELECT 'CREATE DATABASE mlflow' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow') \gexec
