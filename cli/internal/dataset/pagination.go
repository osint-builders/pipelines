package dataset

import "errors"

// Page selects a window after ranking the complete eligible entity pool.
type Page struct {
	Number int
	Size   int
}

func (p Page) Validate() error {
	if p.Size < 1 || p.Size > 100 {
		return errors.New("limit must be between 1 and 100")
	}
	if p.Number < 1 {
		return errors.New("page must be at least 1")
	}
	return nil
}

// RankedPage retains global leaders for query decisions. Image and generated
// text searches resolve leader provenance only when calibration requires it.
type RankedPage[T any] struct {
	Results []T
	Leaders []T
	Total   int
	HasMore bool
}

func paginate[T any](rows []T, page Page) RankedPage[T] {
	result := RankedPage[T]{Results: []T{}, Leaders: rows[:min(2, len(rows))], Total: len(rows)}
	// Check the page against the pool before multiplication to avoid overflow.
	if len(rows) == 0 || page.Number-1 > (len(rows)-1)/page.Size {
		return result
	}
	start := (page.Number - 1) * page.Size
	end := min(start+page.Size, len(rows))
	result.Results, result.HasMore = rows[start:end], end < len(rows)
	return result
}

func (d *Dataset) SearchPage(vector []float32, query string, hybrid bool, filter Filter, page Page) (RankedPage[Result], error) {
	if err := page.Validate(); err != nil {
		return RankedPage[Result]{}, err
	}
	rows, err := d.search(vector, query, hybrid, filter, len(d.Entities), "")
	if err != nil {
		return RankedPage[Result]{}, err
	}
	return paginate(rows, page), nil
}
