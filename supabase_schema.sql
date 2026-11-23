-- ====================================================================
-- CLIPFORGE SUPABASE SCHEMA: MULTI-USER AUTH, QUOTAS & ISOLATED LIBRARY
-- ====================================================================
-- Run this in your Supabase SQL Editor (Dashboard -> SQL Editor)

-- 1. USER PROFILES TABLE (Plans, Quotas & Minutes Tracker)
CREATE TABLE IF NOT EXISTS public.user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    plan TEXT NOT NULL DEFAULT 'free',                 -- 'free', 'pro', 'creator'
    plan_status TEXT NOT NULL DEFAULT 'active',        -- 'active', 'exhausted', 'cancelled'
    free_minutes_total NUMERIC NOT NULL DEFAULT 60.0,  -- 60 free minutes granted
    minutes_used NUMERIC NOT NULL DEFAULT 0.0,        -- minutes consumed so far
    plan_selected_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for speedy profile queries
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON public.user_profiles(email);

-- 2. USER JOBS TABLE (Private Video Processing & Saved Clips per User)
CREATE TABLE IF NOT EXISTS public.user_jobs (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    url TEXT,
    title TEXT,
    thumbnail TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'Queued in processing pool...',
    progress INTEGER NOT NULL DEFAULT 0,
    video_duration_seconds NUMERIC DEFAULT 0,
    minutes_deducted NUMERIC DEFAULT 0,
    options JSONB DEFAULT '{}'::jsonb,
    clips JSONB DEFAULT '[]'::jsonb,
    candidates JSONB DEFAULT '[]'::jsonb,
    error TEXT,
    done BOOLEAN NOT NULL DEFAULT FALSE,
    cancelled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index by user and creation date for private library queries
CREATE INDEX IF NOT EXISTS idx_user_jobs_user_id ON public.user_jobs(user_id, created_at DESC);

-- 3. ROW LEVEL SECURITY (RLS) POLICIES
ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_jobs ENABLE ROW LEVEL SECURITY;

-- User Profiles RLS
DROP POLICY IF EXISTS "Users can view own profile" ON public.user_profiles;
CREATE POLICY "Users can view own profile"
    ON public.user_profiles FOR SELECT
    USING (auth.uid() = id);

DROP POLICY IF EXISTS "Users can update own profile" ON public.user_profiles;
CREATE POLICY "Users can update own profile"
    ON public.user_profiles FOR UPDATE
    USING (auth.uid() = id);

-- User Jobs RLS (Users ONLY see and manage their own clips)
DROP POLICY IF EXISTS "Users can view own jobs" ON public.user_jobs;
CREATE POLICY "Users can view own jobs"
    ON public.user_jobs FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own jobs" ON public.user_jobs;
CREATE POLICY "Users can insert own jobs"
    ON public.user_jobs FOR INSERT
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own jobs" ON public.user_jobs;
CREATE POLICY "Users can update own jobs"
    ON public.user_jobs FOR UPDATE
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can delete own jobs" ON public.user_jobs;
CREATE POLICY "Users can delete own jobs"
    ON public.user_jobs FOR DELETE
    USING (auth.uid() = user_id);

-- 4. AUTOMATIC NEW USER ONBOARDING TRIGGER
-- When a user signs up (via Email/Password or Google), create a free profile with 60 minutes
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.user_profiles (id, email, plan, free_minutes_total, minutes_used)
    VALUES (NEW.id, NEW.email, 'free', 60.0, 0.0)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- 5. ATOMIC QUOTA DEDUCTION FUNCTION
CREATE OR REPLACE FUNCTION public.deduct_user_minutes(p_user_id UUID, p_minutes NUMERIC)
RETURNS TABLE(allowed BOOLEAN, minutes_remaining NUMERIC, minutes_used NUMERIC) AS $$
DECLARE
    v_total NUMERIC;
    v_used NUMERIC;
    v_new_used NUMERIC;
    v_remaining NUMERIC;
BEGIN
    SELECT free_minutes_total, user_profiles.minutes_used
    INTO v_total, v_used
    FROM public.user_profiles
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        -- Default fallback if profile was not created yet
        INSERT INTO public.user_profiles (id, free_minutes_total, minutes_used)
        VALUES (p_user_id, 60.0, 0.0);
        v_total := 60.0;
        v_used := 0.0;
    END IF;

    v_new_used := v_used + p_minutes;
    v_remaining := GREATEST(0.0, v_total - v_new_used);

    UPDATE public.user_profiles
    SET minutes_used = v_new_used,
        plan_status = CASE WHEN v_new_used >= v_total THEN 'exhausted' ELSE 'active' END,
        updated_at = NOW()
    WHERE id = p_user_id;

    RETURN QUERY SELECT (v_new_used <= v_total), v_remaining, v_new_used;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
