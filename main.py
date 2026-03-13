#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ======================================================================
# CONFIGURATION PARAMETERS
# ======================================================================

CONFIG = {
    'repetitions': 4,            # how many times each condition × SOA is repeated
    'num_participants': 5,       # default number of participants
    'included_conditions': {
        'inhalation': True,
        'exhalation': True,
        'baseline': True,
    },
    'catch_trial_percentage': 10,
    'soa_conditions_ms': [190, 400, 700, 1000, 1500],
    'jitter_options_ms': [100, 200, 300, 400, 500],
    'debug_mode': True,
    'paths': {
        'experiment_log_dir': r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level1_AudioGeneration\ExperimentLog",
    },
    'box_breathing': {
        'cycle_duration_sec': 8,      # Total duration of one breath cycle in seconds
        'intro_duration_sec': 60,     # Initial settling period before trials begin
        'outro_duration_sec': 30,     # Closing period after trials end
        'alternating_phases': True,   # Alternate between inhale and exhale phases
        'sample_rate': 48000,         # Audio sample rate
    }
}

"""
PPS Design Generator

This script generates experimental design files for a Peripersonal Space (PPS) experiment.
It creates CSV files with precise timing information for audio stimuli, using regular
8-second intervals for box breathing cycles.

Author: AI Assistant
"""

import os
import numpy as np
import pandas as pd
import random
import json
from itertools import product
from datetime import datetime
import traceback
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import itertools

# ======================================================================
# CONFIGURATION PARAMETERS
# ======================================================================

class BreathPhase(Enum):
    INHALE = "inhale"
    EXHALE = "exhale"

class TrialType(Enum):
    INHALATION = "inhalation"
    EXHALATION = "exhalation"
    BASELINE = "baseline"
    CATCH = "catch"

@dataclass
class DesignConfig:
    repetitions: int = 4
    num_participants: int = 5
    included_conditions: Dict[str, bool] = None
    catch_trial_percentage: int = 10
    soa_conditions_ms: List[int] = None
    jitter_options_ms: List[int] = None
    debug_mode: bool = True
    paths: Dict[str, str] = None
    box_breathing: Dict[str, any] = None
    looming_stimuli: List[str] = None

    def __post_init__(self):
        if self.included_conditions is None:
            self.included_conditions = {
                'inhalation': True,
                'exhalation': True,
                'baseline': True,
            }
        if self.soa_conditions_ms is None:
            self.soa_conditions_ms = [190, 400, 700, 1000, 1500]
        if self.jitter_options_ms is None:
            self.jitter_options_ms = [100, 200, 300, 400, 500]
        if self.paths is None:
            self.paths = {
                'experiment_log_dir': r"C:\Users\cogpsy-vrlab\Documents\GitHub\BreathingSpace\Level1_AudioGeneration\ExperimentLog",
            }
        if self.box_breathing is None:
            self.box_breathing = {
                'cycle_duration_sec': 8,
                'intro_duration_sec': 60,
                'outro_duration_sec': 30,
                'alternating_phases': True,
            }
        if self.looming_stimuli is None:
            self.looming_stimuli = ['blue', 'brown', 'pink', 'white']

# ======================================================================
# CLASS: PPSDesignGenerator
# ======================================================================

class PPSDesignGenerator:
    """Generates CSV design files for Peripersonal Space (PPS) experiments with box breathing."""
    
    def __init__(self, config: Optional[DesignConfig] = None):
        self.config = config or DesignConfig()
        
        # Prepare output directory
        self.experiment_log_dir = self.config.paths['experiment_log_dir']
        try:
            os.makedirs(self.experiment_log_dir, exist_ok=True)
            if self.config.debug_mode:
                print(f"Created/verified output directory: {self.experiment_log_dir}")
        except Exception as e:
            print(f"Error creating output directory: {e}")
            raise
        
        # Calculate trial counts
        self.trial_counts = self._calculate_trial_counts()
        self.conditions = [cond for cond, inc in self.config.included_conditions.items() if inc]
        
        # Set seed for reproducibility
        self.base_seed = 42
        
        if self.config.debug_mode:
            print("Initialized PPSDesignGenerator with:")
            print(f"  - Repetitions: {self.config.repetitions}")
            print(f"  - Conditions: {self.conditions}")
            print(f"  - SOA values: {self.config.soa_conditions_ms}")
            print(f"  - Total trials per participant: {sum(self.trial_counts.values())}")
            print(f"  - Output directory: {self.experiment_log_dir}")

    def _calculate_trial_counts(self) -> Dict[str, int]:
        """Calculate how many trials for each condition plus catch trials."""
        reps = self.config.repetitions
        soa_count = len(self.config.soa_conditions_ms)
        stimulus_types = self.config.looming_stimuli
        catch_percentage = self.config.catch_trial_percentage
        
        trial_counts = {}
        for condition, include in self.config.included_conditions.items():
            if include:
                trial_counts[condition] = len(stimulus_types) * soa_count * reps
        
        regular_trials = sum(trial_counts.values())
        catch_trials = int(np.ceil(regular_trials * catch_percentage / 100))
        trial_counts['catch'] = catch_trials
        
        if self.config.debug_mode:
            print("\nTrial count calculation:")
            for cond, count in trial_counts.items():
                print(f"  {cond}: {count} trials")
            print(f"  Total: {sum(trial_counts.values())} trials")
        
        return trial_counts

    def _generate_latin_square(self, n: int) -> List[List[int]]:
        """Generate a Latin square of size n x n."""
        # Create base sequence
        base = list(range(n))
        latin_square = []
        
        # Generate n different sequences
        for i in range(n):
            # Rotate the base sequence
            sequence = base[i:] + base[:i]
            latin_square.append(sequence)
        
        return latin_square

    def _check_sequence_balance(self, sequence: List[str], max_consecutive: int = 3) -> bool:
        """Check if sequence has balanced trial types with no long runs."""
        if not sequence:
            return True
            
        current_run = 1
        current_type = sequence[0]
        
        for trial_type in sequence[1:]:
            if trial_type == current_type:
                current_run += 1
                if current_run > max_consecutive:
                    return False
            else:
                current_run = 1
                current_type = trial_type
                
        return True

    def _check_transition_balance(self, sequence: List[str]) -> bool:
        """Check if transitions between trial types are balanced."""
        if len(sequence) < 2:
            return True
            
        transitions = {}
        for i in range(len(sequence) - 1):
            transition = (sequence[i], sequence[i + 1])
            transitions[transition] = transitions.get(transition, 0) + 1
            
        # Check if all transitions occur roughly equally
        counts = list(transitions.values())
        return max(counts) - min(counts) <= 1

    def _calculate_sequence_entropy(self, sequence: List[str]) -> float:
        """Calculate the entropy of a sequence to measure unpredictability."""
        if not sequence:
            return 0.0
            
        # Count occurrences of each trial type
        counts = {}
        for trial_type in sequence:
            counts[trial_type] = counts.get(trial_type, 0) + 1
            
        # Calculate entropy
        total = len(sequence)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            entropy -= p * np.log2(p)
            
        return entropy

    def generate_breath_timestamps(self) -> pd.DataFrame:
        """Generate timestamps for breath cycles at regular 8-second intervals."""
        total_trials = sum(self.trial_counts.values())
        cycle_sec = self.config.box_breathing['cycle_duration_sec']
        intro_sec = self.config.box_breathing['intro_duration_sec']
        outro_sec = self.config.box_breathing['outro_duration_sec']
        
        total_duration_sec = intro_sec + (total_trials * cycle_sec) + outro_sec
        sample_rate = 48000
        
        timestamps = []
        current_time_sec = intro_sec
        
        for i in range(total_trials):
            minutes = int(current_time_sec / 60)
            seconds = current_time_sec % 60
            timestamp = f"{minutes:02}:{seconds:.1f}"
            
            milliseconds = current_time_sec * 1000
            sample_index = int(current_time_sec * sample_rate)
            
            if self.config.box_breathing['alternating_phases']:
                breathphase = BreathPhase.INHALE if i % 2 == 0 else BreathPhase.EXHALE
            else:
                breathphase = random.choice([BreathPhase.INHALE, BreathPhase.EXHALE])
            
            timestamps.append({
                'timestamp': timestamp,
                'milliseconds': milliseconds,
                'sample_index': sample_index,
                'breathphase': breathphase,
                'trial_index': i
            })
            
            current_time_sec += cycle_sec
        
        timestamps_df = pd.DataFrame(timestamps)
        
        if self.config.debug_mode:
            print(f"\nGenerated {len(timestamps_df)} breath timestamps")
            print(f"First timestamp: {timestamps_df['timestamp'].iloc[0]}")
            print(f"Last timestamp: {timestamps_df['timestamp'].iloc[-1]}")
            print(f"Total audio duration: {total_duration_sec} seconds")
        
        return timestamps_df

    def generate_counterbalanced_design(self, participant_id: int) -> pd.DataFrame:
        """Generate a counterbalanced design for one participant."""
        random.seed(self.base_seed + participant_id)
        np.random.seed(self.base_seed + participant_id)
        
        all_trials = []
        trial_types = [t for t, c in self.trial_counts.items() if c > 0]
        
        # Generate Latin square for trial type ordering
        latin_square = self._generate_latin_square(len(trial_types))
        participant_sequence = latin_square[participant_id % len(latin_square)]
        
        # Create trial pool for each type
        trial_pools = {}
        for trial_type in trial_types:
            if trial_type == 'catch':
                continue
                
            pool = []
            for stim_type in self.config.looming_stimuli:
                for soa in self.config.soa_conditions_ms:
                    for _ in range(self.config.repetitions):
                        pool.append({
                            'trial_type': trial_type,
                            'stimulus_type': stim_type,
                            'soa_value_ms': soa,
                            'jitter_ms': random.choice(self.config.jitter_options_ms),
                            'is_tactile': True
                        })
            random.shuffle(pool)
            trial_pools[trial_type] = pool
        
        # Create catch trial pool
        catch_pool = []
        for stim_type in self.config.looming_stimuli:
            for soa in self.config.soa_conditions_ms:
                catch_pool.append({
                    'trial_type': 'catch',
                    'stimulus_type': stim_type,
                    'soa_value_ms': soa,
                    'jitter_ms': random.choice(self.config.jitter_options_ms),
                    'is_tactile': False
                })
        random.shuffle(catch_pool)
        trial_pools['catch'] = catch_pool
        
        # Build sequence with balancing constraints
        sequence = []
        while any(pool for pool in trial_pools.values()):
            # Try each trial type in the Latin square order
            for trial_type_idx in participant_sequence:
                trial_type = trial_types[trial_type_idx]
                pool = trial_pools[trial_type]
                
                if pool:
                    # Check if adding this trial would violate constraints
                    temp_sequence = sequence + [trial_type]
                    if (self._check_sequence_balance(temp_sequence) and 
                        self._check_transition_balance(temp_sequence)):
                        sequence.append(trial_type)
                        all_trials.append(pool.pop(0))
                        break
        
        # Convert to DataFrame
        design_df = pd.DataFrame(all_trials)
        design_df['participant_id'] = participant_id
        design_df['trial_number'] = range(1, len(design_df) + 1)
        
        # Validate design
        self._validate_design(design_df)
        
        return design_df

    def _validate_design(self, design_df: pd.DataFrame) -> None:
        """Validate the generated design meets all requirements."""
        if not self.config.debug_mode:
            return
            
        print("\nDesign Validation:")
        
        # Check trial type distribution
        print("\nTrial type distribution:")
        print(design_df['trial_type'].value_counts())
        
        # Check stimulus type distribution
        print("\nStimulus type distribution by trial type:")
        for trial_type in design_df['trial_type'].unique():
            if trial_type == 'baseline':
                continue
            subset = design_df[design_df['trial_type'] == trial_type]
            print(f"  {trial_type}: {subset['stimulus_type'].value_counts().to_dict()}")
        
        # Check SOA distribution
        print("\nSOA distribution by trial type:")
        for trial_type in design_df['trial_type'].unique():
            if trial_type == 'baseline':
                continue
            subset = design_df[design_df['trial_type'] == trial_type]
            print(f"  {trial_type}: {subset['soa_value_ms'].value_counts().to_dict()}")
        
        # Check sequence balance
        sequence = design_df['trial_type'].tolist()
        print(f"\nSequence entropy: {self._calculate_sequence_entropy(sequence):.3f}")
        print(f"Max consecutive same type: {max(len(list(g)) for _, g in itertools.groupby(sequence))}")

    def assign_breath_holds(self, design_df: pd.DataFrame) -> pd.DataFrame:
        """Assign each trial to a specific timestamp from our generated 8-second intervals."""
        timestamps_df = self.generate_breath_timestamps()
        design_with_timestamps = design_df.copy()
        
        # Separate timestamps by breath phase
        inhale_timestamps = timestamps_df[timestamps_df['breathphase'] == BreathPhase.INHALE]
        exhale_timestamps = timestamps_df[timestamps_df['breathphase'] == BreathPhase.EXHALE]
        
        # Create mapping dictionaries
        inhale_lookup = inhale_timestamps.to_dict('records')
        exhale_lookup = exhale_timestamps.to_dict('records')
        
        # Shuffle both lists
        random.shuffle(inhale_lookup)
        random.shuffle(exhale_lookup)
        
        def assign_timestamp(row):
            trial_type = row['trial_type']
            
            if trial_type == 'inhalation' and inhale_lookup:
                timestamp_data = inhale_lookup.pop(0)
            elif trial_type == 'exhalation' and exhale_lookup:
                timestamp_data = exhale_lookup.pop(0)
            elif inhale_lookup:
                timestamp_data = inhale_lookup.pop(0)
            elif exhale_lookup:
                timestamp_data = exhale_lookup.pop(0)
            else:
                raise ValueError("Ran out of timestamps for trial assignment!")
            
            return pd.Series({
                'breathphase': timestamp_data['breathphase'],
                'milliseconds': timestamp_data['milliseconds'],
                'timestamp_original': timestamp_data['timestamp'],
                'sample_index': timestamp_data['sample_index']
            })
        
        # Apply timestamp assignment
        timestamp_cols = design_with_timestamps.apply(assign_timestamp, axis=1)
        design_with_timestamps = pd.concat([design_with_timestamps, timestamp_cols], axis=1)
        
        # Calculate jittered timestamps
        design_with_timestamps['jittered_ms'] = (
            design_with_timestamps['milliseconds'] + 
            design_with_timestamps['jitter_ms']
        )
        
        # Format timestamps
        def ms_to_timestamp(ms):
            m = int(ms // 60000)
            s = (ms % 60000) / 1000.0
            return f"{m:02}:{s:.1f}"
        
        design_with_timestamps['timestamp_after_jitter'] = (
            design_with_timestamps['jittered_ms'].apply(ms_to_timestamp)
        )
        
        # Add SOA timestamps for non-catch trials
        non_catch_mask = design_with_timestamps['trial_type'] != 'catch'
        design_with_timestamps.loc[non_catch_mask, 'soa_ms'] = (
            design_with_timestamps.loc[non_catch_mask, 'jittered_ms'] + 
            design_with_timestamps.loc[non_catch_mask, 'soa_value_ms']
        )
        
        design_with_timestamps.loc[non_catch_mask, 'timestamp_with_soa'] = (
            design_with_timestamps.loc[non_catch_mask, 'soa_ms'].apply(ms_to_timestamp)
        )
        
        return design_with_timestamps

    def finalize_design_csv(self, design_df: pd.DataFrame) -> pd.DataFrame:
        """Finalize the design CSV with proper column names and special case handling."""
        # Rename columns
        design_df.rename(columns={
            'stimulus_type': 'looming_stimulus_type',
            'timestamp_original': 'retentionphase_timestamp',
            'timestamp_after_jitter': 'looming_stimulus_timestamp',
            'timestamp_with_soa': 'tactile_stimulus_timestamp'
        }, inplace=True)
        
        # Set baseline trials - no looming
        baseline_mask = (design_df['trial_type'] == 'baseline')
        design_df.loc[baseline_mask, 'looming_stimulus_type'] = 'none'
        
        return design_df

    def generate_participant_design(self, participant_id: int) -> pd.DataFrame:
        """Generate complete design for one participant."""
        try:
            # Generate counterbalanced design
            design_df = self.generate_counterbalanced_design(participant_id)
            
            # Assign breath holds
            design_df = self.assign_breath_holds(design_df)
            
            # Finalize design
            design_df = self.finalize_design_csv(design_df)
            
            # Ensure all required columns are present
            required_columns = [
                'participant_id',      # Unique identifier for each participant
                'trial_number',        # Sequential number of the trial
                'trial_type',          # Type of trial (inhalation/exhalation/baseline/catch)
                'looming_stimulus_type', # Type of looming stimulus
                'soa_value_ms',        # Time between looming and tactile
                'jitter_ms',           # Random jitter added to timestamps
                'is_tactile',          # Whether trial includes tactile stimulus
                'breathphase',         # Current breath phase
                'retentionphase_timestamp', # Original timestamp before jitter
                'looming_stimulus_timestamp', # Timestamp for looming stimulus
                'tactile_stimulus_timestamp'  # Timestamp for tactile stimulus
            ]
            
            missing_columns = [col for col in required_columns if col not in design_df.columns]
            if missing_columns:
                raise ValueError(f"Missing required columns: {missing_columns}")
            
            # Save to CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"pps_design_p{participant_id:02d}_{timestamp}.csv"
            filepath = os.path.join(self.experiment_log_dir, filename)
            
            # Save with proper encoding and line endings
            design_df.to_csv(filepath, index=False, encoding='utf-8', line_terminator='\n')
            
            if self.config.debug_mode:
                print(f"\nSaved design to: {filepath}")
                print(f"Number of trials: {len(design_df)}")
                print(f"Columns: {', '.join(design_df.columns)}")
            
            return design_df
            
        except Exception as e:
            print(f"Error generating design for participant {participant_id}:")
            print(traceback.format_exc())
            raise

    def generate_all_participants(self) -> List[pd.DataFrame]:
        """Generate designs for all participants."""
        designs = []
        for participant_id in range(1, self.config.num_participants + 1):
            try:
                design = self.generate_participant_design(participant_id)
                designs.append(design)
            except Exception as e:
                print(f"Failed to generate design for participant {participant_id}")
                raise
        return designs

# ======================================================================
# AUDIO GENERATION FUNCTIONS
# ======================================================================

def generate_audio_from_design(design_csv_path, output_dir, base_audio_path=None):
    """
    Generate audio files from a design CSV.
    
    This is a placeholder for audio generation functionality.
    The actual implementation would:
    1. Read the design CSV
    2. Create or load the base box breathing audio
    3. Insert looming and tactile stimuli at the specified timestamps
    4. Save the resulting audio files
    
    Args:
        design_csv_path: Path to the design CSV
        output_dir: Directory to save audio files
        base_audio_path: Optional path to base box breathing audio
        
    Returns:
        Tuple of (looming_audio_path, tactile_audio_path)
    """
    # This is just a placeholder - implement actual audio generation here
    print(f"Audio generation from {design_csv_path} not implemented yet")
    return None, None

# ======================================================================
# MAIN FUNCTION
# ======================================================================

def main():
    """Main function to run the design generator."""
    # Create the design generator
    generator = PPSDesignGenerator(CONFIG)
    
    # Run for all participants defined in CONFIG
    results = generator.run()
    
    # Print summary
    print("\n=== Generation Summary ===")
    for pid, info in results.items():
        if info['success']:
            print(f"Participant {pid}: CSV generated at {info['csv_path']}")
        else:
            print(f"Participant {pid}: ERROR - no CSV generated.")
    
    print("\nTo generate audio files from these designs, implement the audio generation function.")

if __name__ == "__main__":
    main()