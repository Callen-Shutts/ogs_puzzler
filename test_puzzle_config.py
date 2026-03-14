#!/usr/bin/env python3
"""
Test script to validate the KataGo puzzle-finding configuration.
This script checks that the key parameters are set correctly for puzzle solving.
"""

import re
import sys

def test_puzzle_configuration():
    """Test that the configuration file has the right settings for puzzle finding."""

    try:
        with open('kata/default_gtp.cfg', 'r') as f:
            config_content = f.read()
    except FileNotFoundError:
        print("ERROR: Configuration file not found at kata/default_gtp.cfg")
        return False

    # Test key parameters for puzzle finding
    tests = [
        (r'maxVisits\s*=\s*20', "maxVisits should be 20 for quick evaluation"),
        (r'maxPlayouts\s*=\s*50', "maxPlayouts should be 50 for limited analysis"),
        (r'maxTime\s*=\s*5\.0', "maxTime should be 5.0 seconds for fast response"),
        (r'numSearchThreads\s*=\s*4', "numSearchThreads should be 4 for faster processing"),
        (r'analysisWideRootNoise\s*=\s*0\.10', "analysisWideRootNoise should be 0.10 for wider exploration"),
        (r'analysisIgnorePreRootHistory\s*=\s*true', "analysisIgnorePreRootHistory should be true to focus on current position"),
    ]

    all_passed = True

    print("Testing KataGo puzzle-finding configuration...")
    print("=" * 50)

    for pattern, description in tests:
        if re.search(pattern, config_content):
            print(f"✓ PASS: {description}")
        else:
            print(f"✗ FAIL: {description}")
            all_passed = False

    print("=" * 50)

    if all_passed:
        print("SUCCESS: All puzzle-finding configuration tests passed!")
        print("\nThis configuration is optimized for finding easy puzzles quickly.")
        print("Key features:")
        print("- Low visit and playout limits for fast evaluation")
        print("- Reduced search threads for quicker response")
        print("- Higher analysis noise for wider move exploration")
        print("- Position history ignoring for focused puzzle solving")
        return True
    else:
        print("FAILURE: Some configuration parameters are not set correctly for puzzle finding.")
        return False

if __name__ == "__main__":
    success = test_puzzle_configuration()
    sys.exit(0 if success else 1)
