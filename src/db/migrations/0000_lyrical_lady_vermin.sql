CREATE TABLE `media_items` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`profile_id` integer NOT NULL,
	`platform` text NOT NULL,
	`post_id` text NOT NULL,
	`post_url` text,
	`media_type` text NOT NULL,
	`media_url` text NOT NULL,
	`content_hash` text,
	`file_size` integer,
	`width` integer,
	`height` integer,
	`duration` real,
	`caption` text,
	`posted_at` integer,
	`status` text DEFAULT 'pending' NOT NULL,
	`local_path` text,
	`gdrive_file_id` text,
	`gdrive_url` text,
	`error_message` text,
	`retry_count` integer DEFAULT 0 NOT NULL,
	`discovered_at` integer NOT NULL,
	`downloaded_at` integer,
	`uploaded_at` integer,
	FOREIGN KEY (`profile_id`) REFERENCES `profiles`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_media_dedup` ON `media_items` (`profile_id`,`post_id`,`media_url`);--> statement-breakpoint
CREATE INDEX `idx_media_status` ON `media_items` (`status`);--> statement-breakpoint
CREATE INDEX `idx_media_profile` ON `media_items` (`profile_id`);--> statement-breakpoint
CREATE TABLE `profiles` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`platform` text NOT NULL,
	`username` text NOT NULL,
	`profile_url` text NOT NULL,
	`display_name` text,
	`avatar_url` text,
	`is_active` integer DEFAULT true NOT NULL,
	`scrape_interval_minutes` integer DEFAULT 360 NOT NULL,
	`last_scraped_at` integer,
	`gdrive_folder_id` text,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_profiles_platform_username` ON `profiles` (`platform`,`username`);--> statement-breakpoint
CREATE TABLE `scrape_jobs` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`profile_id` integer NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`triggered_by` text NOT NULL,
	`media_found` integer DEFAULT 0 NOT NULL,
	`media_new` integer DEFAULT 0 NOT NULL,
	`media_downloaded` integer DEFAULT 0 NOT NULL,
	`media_uploaded` integer DEFAULT 0 NOT NULL,
	`error_message` text,
	`started_at` integer,
	`completed_at` integer,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`profile_id`) REFERENCES `profiles`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `idx_jobs_profile` ON `scrape_jobs` (`profile_id`);--> statement-breakpoint
CREATE INDEX `idx_jobs_status` ON `scrape_jobs` (`status`);