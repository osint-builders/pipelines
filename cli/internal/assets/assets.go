package assets

import "embed"

// Files contains the immutable release dataset. Development builds show help without it.
//
//go:embed data/*
var Files embed.FS
