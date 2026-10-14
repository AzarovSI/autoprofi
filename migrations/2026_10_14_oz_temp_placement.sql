-- MPPROFIT Ozon: «Временное размещение» (секция «Удержания OZON») по товарам.
-- Входит в «Все удержания OZON» и выплату; в юнит-экономике — в «Хранение и проч.».
-- Для WB/Яндекса и периодов, загруженных до миграции, — NULL.
ALTER TABLE fact_weekly  ADD COLUMN IF NOT EXISTS oz_temp_placement numeric;
ALTER TABLE fact_monthly ADD COLUMN IF NOT EXISTS oz_temp_placement numeric;
