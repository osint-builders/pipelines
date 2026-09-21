package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/osint-builders/pipelines/cli/internal/dataset"
)

func isResearchCommand(command string) bool {
	return command == "facts" || command == "relationships" || command == "compare" || command == "list"
}

func validateResearchArguments(command string, args []string, relationType string, explicitType bool) error {
	if !isResearchCommand(command) {
		return nil
	}
	switch command {
	case "list":
		if len(args) != 0 {
			return errors.New("list accepts filters and no query or ID; place flags before arguments")
		}
	case "facts", "relationships":
		if len(args) != 1 || strings.TrimSpace(args[0]) == "" {
			return fmt.Errorf("%s requires one nonempty entity ID; place flags before the ID", command)
		}
	case "compare":
		if len(args) < 2 || len(args) > 20 {
			return errors.New("compare requires 2 to 20 unique entity IDs")
		}
		seen := map[string]bool{}
		for _, id := range args {
			if strings.TrimSpace(id) == "" || seen[id] {
				return errors.New("compare requires nonempty, unique entity IDs")
			}
			seen[id] = true
		}
	}
	if explicitType {
		switch relationType {
		case "equivalent", "related_system", "variant_of", "family_member_of", "component_of":
		default:
			return errors.New("--type must be equivalent, related_system, variant_of, family_member_of, or component_of")
		}
	}
	return nil
}

func runResearch(d *dataset.Dataset, command string, args []string, relationType string, filter dataset.Filter, limit int, output *json.Encoder) error {
	response := map[string]any{"dataset_id": d.Manifest.DatasetID}
	switch command {
	case "facts":
		claims, err := d.ResearchFacts(args[0])
		if err != nil {
			return err
		}
		response["entity_id"], response["claims"] = args[0], claims
	case "relationships":
		relations, err := d.Relationships(args[0], relationType)
		if err != nil {
			return err
		}
		response["entity_id"], response["relationships"] = args[0], relations
	case "compare":
		fields, err := d.Compare(args)
		if err != nil {
			return err
		}
		response["entity_ids"], response["fields"] = args, fields
	case "list":
		entities, total, err := d.List(filter, limit)
		if err != nil {
			return err
		}
		response["total"], response["results"] = total, entities
	default:
		return fmt.Errorf("unsupported research command: %s", command)
	}
	output.SetEscapeHTML(false)
	return output.Encode(response)
}
