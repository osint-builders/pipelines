package main

import (
	"flag"
	"fmt"
	"io"
	"strings"
)

const help = `pipeline - offline search across the dataset

Usage:
  pipeline search "query" [options]
  pipeline search --image photo.jpg [options]
  pipeline get SOURCE:ID
  pipeline info

Examples:
  pipeline search "airborne radar"
  pipeline search "airborne radar" --mode vector --page 2 --limit 20
  pipeline search "airborne radar" --raw
  pipeline search --image radar.jpg

Search covers every source by default. Results are JSON.
Use pipeline search --help for search options, or --version for the version.
`

const searchHelp = `Usage: pipeline search [query words] [options]

Search every source by meaning and keyword/name matches. Quote a phrase or type
several words. Options work before or after the query. Use -- before query words
that start with a dash.

  --mode hybrid|vector  Hybrid ranking (default), or semantic vector similarity
  --image PATH          Search using a local JPEG/PNG; optional text refines it
  --page N              Result page, starting at 1 (default 1)
  --limit N             Results per page, 1-100 (default 10)
  --raw                 Include each result's complete saved record and evidence
  --source SOURCE       Restrict to one source; omitted means all sources
  --kind KIND           Restrict to an entity kind
  --where "FIELD OP VALUE"
                        Require a field value; repeat to combine conditions

Examples:
  pipeline search coastal surveillance --page 2
  pipeline search "phased array" --mode vector --raw --limit 3
  pipeline search --image radar.jpg
  pipeline search "radar" --where "range>=100 km"

Image lookup ranks visual similarity, not verified identity. Images may be up to
20 MiB and 40 million pixels. --mode selects text-only ranking; image + text uses
hybrid fusion. Text queries allow 1,000 characters and 256 model tokens.
Use pipeline info to list sources, kinds, and filter fields.
`

func commandHelp(command string, out io.Writer) error {
	var body string
	switch command {
	case "search":
		body = searchHelp
	case "get":
		body = "Usage: pipeline get SOURCE:ID [--format json|markdown|html|source] [--evidence PAGE_ID]\n\nShow a complete saved record (JSON by default). Select an evidence page for HTML\nor exact source-byte export when a record has several pages.\n"
	case "info":
		body = "Usage: pipeline info\n\nShow dataset coverage, available sources, entity kinds, and filter fields.\n"
	default:
		body = help
	}
	_, err := io.WriteString(out, body)
	return err
}

// Go's flag parser stops at the first positional argument. Separate flags from
// query words without guessing whether a flag consumes a value.
func parseFlags(flags *flag.FlagSet, args []string) error {
	var options, words []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--" {
			words = append(words, args[i+1:]...)
			break
		}
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			words = append(words, arg)
			continue
		}
		name := strings.TrimPrefix(strings.TrimPrefix(arg, "-"), "-")
		name, _, attached := strings.Cut(name, "=")
		if name == "help" || name == "h" {
			return flag.ErrHelp
		}
		f := flags.Lookup(name)
		if f == nil {
			return fmt.Errorf("unknown option: %s (see pipeline %s --help)", arg, flags.Name())
		}
		options = append(options, arg)
		boolean, ok := f.Value.(interface{ IsBoolFlag() bool })
		if !attached && !(ok && boolean.IsBoolFlag()) {
			if i+1 == len(args) {
				return fmt.Errorf("option --%s requires a value", name)
			}
			i++
			options = append(options, args[i])
		}
	}
	return flags.Parse(append(append(options, "--"), words...))
}
