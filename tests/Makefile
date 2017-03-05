
include ../Makefile.inc

SRC=../gdctools
TEST_ROOT=$(ROOT)/tests/$(shell grep ROOT_DIR: $(CONFIG_FILE) | awk '{print $$NF}')
CONFIG_FILE=tcgaSmoketest.cfg
CONFIG_FLAG=--config $(CONFIG_FILE)
FIND_MIRROR_FILES=cd $(TEST_ROOT)/mirror/TCGA && find TCGA-* \
							-name '*.gz' -o -name '*.xml' -o -name '*.txt'
FIND_DICE_FILES=cd $(TEST_ROOT)/dice/TCGA && find TCGA-* -name '*.txt'
PERFORM_MIRROR=$(PYTHON) $(SRC)/gdc_mirror.py $(CONFIG_FLAG)
PERFORM_DICE=$(PYTHON) $(SRC)/gdc_dice.py $(CONFIG_FLAG) --projects TCGA-ACC TCGA-OV

help:
	@echo
	@echo "Run various GDCtools tests.  Requires GNUmake 3.81 or later"
	@echo
	@echo "Targets:"
	@echo
	@echo  "1. test                     Exercise tests for this package"
	@echo  "2. install                  Install locally, using pip"
	@echo  "3. uninstall                Remove local install, using pip"
	@echo  "4. publish                  Submit to PyPI"
	@echo


test: test_smoke echo_success

test_all: test_smoke test_dice test_loadfile test_report echo_success

test_smoke: test_invoke test_mirror test_redo_mirror

test_invoke:
	@echo
	@echo Test runnability: invoke some tools to show nothing thrown to stderr
	@$(PYTHON) $(SRC)/GDCcli.py >/dev/null
	@$(PYTHON) $(SRC)/GDCtool.py >/dev/null
	@$(PYTHON) $(SRC)/gdc_ls.py --help >/dev/null

test_mirror:
	@echo
	@echo "Test mirror: download small set of data, compare to baselines"
	@$(PERFORM_MIRROR) 2>&1 | egrep "GDC|Mirroring data|Mirroring start"

	@# Now see that the named set of mirrored files matches what we expect
	$(FIND_MIRROR_FILES) | sort > $(TEST_ROOT)/mirror-files.txt
	diff -b baselines/mirror-files.txt $(TEST_ROOT)/.

	@# Verify integrity (but using our stored MD5s, not those just downloaded)
	$(FIND_MIRROR_FILES) -exec $(MD5) {} \; | sort > $(TEST_ROOT)/mirror-md5sums.txt
	diff -b baselines/mirror-md5sums.txt $(TEST_ROOT)/.

test_redo_mirror:
	@echo
	@echo Test retry of mirror: nothing should be re-downloaded
	@$(PERFORM_MIRROR) 2>&1 | grep -w new | grep -vl " [^0][0-9]* new " >/dev/null

test_dice:
	@echo
	@echo Test dice: on subset of cohorts, to show CLI args override config file
	$(PERFORM_DICE) > dice.log 2>&1

	$(FIND_DICE_FILES) | sort > $(TEST_ROOT)/dice-files.txt
	diff -b baselines/dice-files.txt $(TEST_ROOT)/.

	@# Verify integrity (but using our stored MD5s, not those just downloaded)
	$(FIND_DICE_FILES) -exec $(MD5) {} \; | sort > $(TEST_ROOT)/dice-md5sums.txt
	diff -b baselines/dice-md5sums.txt $(TEST_ROOT)/.

test_loadfile:
	$(PYTHON) $(SRC)/create_loadfile.py $(CONFIG_FLAG)

test_report:
	$(PYTHON) $(SRC)/sample_report.py $(CONFIG_FLAG)

USE=/broad/tools/scripts/useuse
test3: default
	@# Python 3 compatibility
	if [ -d $(USE) ] ; then \
		. $(USE) && \
		reuse -q Python-3.4 && \
		$(MAKE) -e test ; \
	fi

VERTEST="import gdctools as g; print('Version: ' + g.GDCcore.GDCT_VERSION)"
testl: default
	@# Test the package locally, as if it were installed
	@$(PYTHON) -c  $(VERTEST)

testi:
	@# Test the installed package
	@(cd /tmp ; $(PYTHON) -c $(VERTEST))

.PHONY: test clean echo_success test_mirror test_dice test_redo_mirror

echo_success:
	@echo
	@echo Success!

clean:
	rm -rf build dist *.egg-info *~ *mirror*log *dice*log

rclean: clean
	\rm -rf sandbox GDCtool