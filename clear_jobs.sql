-- SQL script to delete all jobs and job files from the database
-- Run this with: psql -d reel_generator -f clear_jobs.sql
-- Or connect to your database and run these commands

-- Delete all job files first (due to foreign key constraint)
DELETE FROM job_files;

-- Delete all jobs
DELETE FROM jobs;

-- Verify deletion
SELECT COUNT(*) as remaining_jobs FROM jobs;
SELECT COUNT(*) as remaining_job_files FROM job_files;

