BUILD_DIR := build
SPEC_SRC := specs/Remove Implicit Parent Project Property Lookup - GBT Spec.md
SPEC_PDF := $(BUILD_DIR)/spec.pdf
TAKEAWAYS_SRC := specs/Remove Implicit Parent Project Property Lookup - Show & Tell Takeaways.md
TAKEAWAYS_PDF := $(BUILD_DIR)/takeaways.pdf

.PHONY: pdf spec takeaways preview clean

pdf: spec takeaways

spec:
	mkdir -p $(BUILD_DIR)
	mdspec convert "$(SPEC_SRC)" -o "$(SPEC_PDF)"

takeaways:
	mkdir -p $(BUILD_DIR)
	mdspec convert "$(TAKEAWAYS_SRC)" -o "$(TAKEAWAYS_PDF)"

preview: spec
	pdftoppm -f 1 -l 1 -png -r 160 -singlefile $(SPEC_PDF) $(BUILD_DIR)/page-1

clean:
	rm -rf $(BUILD_DIR)
