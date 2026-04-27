SOURCE ?= specs/Remove Implicit Parent Project Property Lookup - GBT Spec.md
BUILD_DIR := build
TYP := $(BUILD_DIR)/spec.typ
PDF := $(BUILD_DIR)/spec.pdf
.PHONY: pdf preview clean

pdf: $(PDF)

$(PDF): $(TYP)
	typst compile $(TYP) $(PDF)

$(TYP): scripts/md_to_typst.py
	mkdir -p $(BUILD_DIR)
	.venv/bin/python scripts/md_to_typst.py "$(SOURCE)" --output "$(TYP)"

preview: $(PDF)
	pdftoppm -f 1 -l 1 -png -r 160 -singlefile $(PDF) $(BUILD_DIR)/page-1

clean:
	rm -rf $(BUILD_DIR)
