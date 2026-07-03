-- Seed data for local/dev environments.
-- 20 vehicles (mixed European makes/models, 2015-2025) + 2 sample app_events.

insert into vehicles (vin, make, model, year, price_cents, mileage_km, status) values
  ('VF1HBUSRJGF4CBFPR', 'Renault',     'Clio',    2019, 1299000,  48000, 'available'),
  ('VF19BN3R5UAL4YUKP', 'Renault',     'Megane',  2021, 1649000,  32000, 'available'),
  ('VF1YGF1GZZTC6H1FV', 'Renault',     'Captur',  2022, 1990000,  21000, 'available'),
  ('WVW0NECRVFRG1U60L', 'Volkswagen',  'Golf',    2020, 1899000,  35000, 'reserved'),
  ('WVW0ZPUELSL61URXD', 'Volkswagen',  'Polo',    2018, 1149000,  62000, 'available'),
  ('WVWRCX2UEPXP826KT', 'Volkswagen',  'Passat',  2017, 1399000,  89000, 'reserved'),
  ('WVWJST420RJ98FDHK', 'Volkswagen',  'Tiguan',  2023, 2890000,  12000, 'available'),
  ('VF3L4E116TAHUYHV4', 'Peugeot',     '208',     2022, 1549000,  18000, 'available'),
  ('VF3L6AT9M9GW9NK0L', 'Peugeot',     '308',     2019, 1429000,  55000, 'sold'),
  ('VF3AX8BH0WSDSFF8E', 'Peugeot',     '3008',    2021, 2290000,  29000, 'available'),
  ('WBAJJ7LT4PNW2055H', 'BMW',         '320i',    2020, 2499000,  41000, 'reserved'),
  ('WBASREYBRRAEDRECY', 'BMW',         'X1',      2023, 3490000,   9000, 'available'),
  ('TMBE9SU8PJ7S73NGG', 'Skoda',       'Octavia', 2018, 1349000,  71000, 'available'),
  ('TMB4Z436DGD2YGSNN', 'Skoda',       'Fabia',   2021, 1229000,  27000, 'available'),
  ('VNK5J4MU6SE5GDAFS', 'Toyota',      'Corolla', 2022, 2049000,  16000, 'available'),
  ('VNKL387P2DL1A1T6V', 'Toyota',      'Yaris',   2019, 1199000,  49000, 'sold'),
  ('VNK48KNVPDDXDD79L', 'Toyota',      'RAV4',    2024, 3390000,   5000, 'available'),
  ('WAUD9FMEES2HSCF3X', 'Audi',        'A3',      2020, 2149000,  37000, 'reserved'),
  ('WF0TPXST2JW6XEA6G', 'Ford',        'Focus',   2016,  899000, 102000, 'sold'),
  ('W0LEP9TJZES0VL5WA', 'Opel',        'Corsa',   2015,  699000, 118000, 'available')
on conflict (vin) do nothing;

insert into app_events (kind, message, context, status) values
  ('bug_report', 'Vehicle photo upload fails with a 500 error when attaching more than 5 images to a single listing.', '{}', 'new'),
  ('bug_report', 'Filtering the inventory list by price range does not reset when clearing filters; stale results stay on screen.', '{}', 'new');
