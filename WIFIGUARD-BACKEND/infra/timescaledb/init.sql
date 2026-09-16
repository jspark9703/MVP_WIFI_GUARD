CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS presence_samples (
  ts timestamptz NOT NULL,
  facility_id text NOT NULL,
  device_id text NOT NULL,
  state text NOT NULL,
  mv_current double precision,
  wander_current double precision,
  mv_threshold double precision,
  wander_baseline double precision,
  wander_ratio_threshold double precision,
  wander_ratio double precision,
  wander_confirmed boolean,
  last_activity_at double precision,
  seconds_since_activity double precision,
  just_changed boolean
);

SELECT create_hypertable('presence_samples', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS presence_samples_device_ts
  ON presence_samples (facility_id, device_id, ts DESC);
