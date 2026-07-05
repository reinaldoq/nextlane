import { createClient } from '@supabase/supabase-js'
import { AUTH_STORAGE_KEY } from './authStorage'

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL as string,
  import.meta.env.VITE_SUPABASE_ANON_KEY as string,
  { auth: { storageKey: AUTH_STORAGE_KEY } }, // deterministic key: e2e injects here
)
