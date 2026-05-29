-- Chạy trong Supabase SQL Editor để tạo bảng market_share_daily
-- Lưu GTGD HOSE/HNX/SHS theo từng ngày giao dịch

CREATE TABLE IF NOT EXISTS market_share_daily (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  trading_date   date         NOT NULL,
  entity         text         NOT NULL,   -- 'SHS', 'HOSE Total', 'HNX Total'
  gtgd_bil       numeric,
  market_share_pct numeric,
  updated_at     timestamptz  DEFAULT now(),
  UNIQUE (trading_date, entity)
);

-- Index để query nhanh theo ngày
CREATE INDEX IF NOT EXISTS idx_msd_date ON market_share_daily (trading_date DESC);

-- Enable RLS (optional — tùy theo policy hiện tại)
ALTER TABLE market_share_daily ENABLE ROW LEVEL SECURITY;

-- Policy đọc public (nếu dùng anon key từ frontend)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'market_share_daily' AND policyname = 'allow_read_all'
  ) THEN
    EXECUTE 'CREATE POLICY allow_read_all ON market_share_daily FOR SELECT USING (true)';
  END IF;
END
$$;
